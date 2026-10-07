"""Interface web local do robô de candidaturas. Rode: python app.py  →  http://127.0.0.1:5000"""
import csv
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import webbrowser
from collections import Counter
from datetime import date
from pathlib import Path

import yaml
from flask import Flask, jsonify, request, send_file, send_from_directory

BASE = Path(__file__).resolve().parent
CONFIG = BASE / "config.yaml"
APPLIED_CSV = BASE / "applied.csv"
UNANSWERED_CSV = BASE / "unanswered_questions.csv"
PROFILE_DIR = BASE / "profile"
RESUME_DIR = BASE / "resume"

app = Flask(__name__, static_folder=str(BASE / "web"), static_url_path="")
app.json.sort_keys = False  # mantém a ordem do config.yaml


class Runner:
    """Executa autoapply.py em segundo plano e guarda o log."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None
        self.mode = ""
        self.lines: list[str] = []
        self.pending_confirm: dict | None = None
        self.lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, mode: str) -> None:
        if self.running:
            raise RuntimeError("O robô já está rodando.")
        args = [sys.executable, "-u", str(BASE / "autoapply.py")]
        if mode == "login":
            args.append("--login")
        elif mode == "dry":
            args.append("--dry-run")
        env = {**os.environ, "AUTOAPPLY_GUI": "1", "PYTHONIOENCODING": "utf-8"}
        self.proc = subprocess.Popen(args, cwd=BASE, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.mode = mode
        with self.lock:
            self.lines = []
            self.pending_confirm = None
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            line = line.rstrip("\n")
            with self.lock:
                if line.startswith("@@CONFIRM@@"):
                    self.pending_confirm = json.loads(line[len("@@CONFIRM@@"):])
                else:
                    self.lines.append(line)
        with self.lock:
            self.pending_confirm = None
            self.lines.append(f"— processo encerrado (código {proc.wait()}) —")

    def confirm(self, answer: str) -> None:
        with self.lock:
            if not (self.running and self.pending_confirm):
                return
            self.pending_confirm = None
        self.proc.stdin.write(answer + "\n")
        self.proc.stdin.flush()

    def stop(self) -> None:
        if self.running:
            # /T encerra também o navegador aberto pelo Playwright
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)


runner = Runner()


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter=";"))


def load_config() -> dict:
    if not CONFIG.exists():  # config.yaml tem dados pessoais e não vai pro git; nasce do exemplo
        shutil.copy(BASE / "config.example.yaml", CONFIG)
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def save_config(cfg: dict) -> None:
    CONFIG.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")


def linkedin_logged_in() -> bool:
    """Verifica se a sessão salva tem o cookie de login do LinkedIn (li_at) ainda válido."""
    for db in (PROFILE_DIR / "Default" / "Network" / "Cookies", PROFILE_DIR / "Default" / "Cookies"):
        if not db.exists():
            continue
        tmp = Path(tempfile.gettempdir()) / "autoapply_cookies.db"
        try:
            shutil.copy2(db, tmp)  # cópia: o arquivo original fica travado enquanto o navegador está aberto
            con = sqlite3.connect(tmp)
            # expires_utc está em microssegundos desde 1601-01-01
            now = int((time.time() + 11644473600) * 1_000_000)
            row = con.execute("SELECT 1 FROM cookies WHERE host_key LIKE '%linkedin.com' AND name = 'li_at' "
                              "AND (expires_utc = 0 OR expires_utc > ?)", (now,)).fetchone()
            con.close()
            return row is not None
        except (OSError, sqlite3.Error):
            return False
    return False


def extract_resume_info(pdf: Path) -> dict:
    """Extrai sugestões (nome, e-mail, telefone, LinkedIn) do texto do currículo."""
    try:
        from pypdf import PdfReader
        text = "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages[:2])
    except Exception:
        return {}
    info = {}
    if m := re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text):
        info["email"] = m.group(0).rstrip(".")
    if m := re.search(r"(?:\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}", text):
        info["phone"] = re.sub(r"\D", "", m.group(0))
    if m := re.search(r"(?:https?://)?(?:[\w]+\.)?linkedin\.com/in/[\w%-]+/?", text, re.I):
        url = m.group(0)
        info["linkedin_url"] = url if url.startswith("http") else "https://" + url
    for line in text.splitlines():
        line = line.strip()
        # nome = primeira linha "limpa" com 2 a 6 palavras só com letras
        if 2 <= len(line.split()) <= 6 and re.fullmatch(r"[A-Za-zÀ-ÿ' .-]+", line):
            info["full_name"] = line.title() if line.isupper() else line
            break
    return info


def safe_filename(name: str) -> str:
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r"[^\w.-]+", "_", name).strip("._")
    return name if name.lower().endswith(".pdf") else (name or "curriculo") + ".pdf"


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def get_config():
    return jsonify(load_config())


@app.put("/api/config")
def put_config():
    save_config(request.get_json())
    return jsonify(ok=True)


@app.post("/api/resume")
def upload_resume():
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify(error="Envie um arquivo PDF."), 400
    RESUME_DIR.mkdir(exist_ok=True)
    for old in RESUME_DIR.glob("*.pdf"):
        old.unlink()
    # Mantém o nome original: o robô usa esse nome para achar o currículo já enviado ao LinkedIn
    dest = RESUME_DIR / safe_filename(f.filename)
    f.save(dest)
    cfg = load_config()
    cfg["resume_path"] = str(dest).replace("\\", "/")
    save_config(cfg)
    return jsonify(ok=True, path=cfg["resume_path"], name=dest.name, extracted=extract_resume_info(dest))


@app.get("/api/resume/file")
def view_resume():
    path = Path(load_config().get("resume_path") or "")
    if not path.is_file():
        return jsonify(error="Nenhum currículo enviado."), 404
    return send_file(path, mimetype="application/pdf")


@app.delete("/api/resume")
def delete_resume():
    cfg = load_config()
    path = Path(cfg.get("resume_path") or "")
    if path.is_file() and RESUME_DIR in path.parents:
        path.unlink()
    cfg["resume_path"] = ""
    save_config(cfg)
    return jsonify(ok=True)


@app.post("/api/linkedin/logout")
def linkedin_logout():
    if runner.running:
        return jsonify(error="Pare o robô antes de desconectar."), 409
    shutil.rmtree(PROFILE_DIR, ignore_errors=True)
    return jsonify(ok=True)


@app.get("/api/status")
def status():
    since = int(request.args.get("since", 0))
    with runner.lock:
        lines = runner.lines[since:]
        total = len(runner.lines)
        confirm = runner.pending_confirm
    return jsonify(running=runner.running, mode=runner.mode, lines=lines, total=total, confirm=confirm)


@app.get("/api/linkedin/status")
def linkedin_status():
    return jsonify(logged_in=linkedin_logged_in())


@app.post("/api/start")
def start():
    mode = request.get_json().get("mode", "dry")
    if mode != "login":
        cfg = load_config()
        if not linkedin_logged_in():
            return jsonify(error="Conecte sua conta do LinkedIn na aba Meu perfil."), 400
        if cfg.get("data_source", "local") == "local" and not Path(cfg.get("resume_path") or "").is_file():
            return jsonify(error="Envie seu currículo na aba Meu perfil (ou escolha usar os dados do LinkedIn)."), 400
        if not cfg.get("searches"):
            return jsonify(error="Cadastre ao menos uma busca na aba Vagas."), 400
    try:
        runner.start(mode)
    except RuntimeError as e:
        return jsonify(error=str(e)), 409
    return jsonify(ok=True)


@app.post("/api/stop")
def stop():
    runner.stop()
    return jsonify(ok=True)


@app.post("/api/confirm")
def confirm():
    runner.confirm(request.get_json().get("answer", "n"))
    return jsonify(ok=True)


@app.get("/api/applied")
def applied():
    rows = read_csv(APPLIED_CSV)
    today = date.today().isoformat()
    stats = {
        "enviadas_total": sum(r["status"] == "enviada" for r in rows),
        "enviadas_hoje": sum(r["status"] == "enviada" and r["data"].startswith(today) for r in rows),
        "puladas": sum(r["status"] == "pulada" for r in rows),
        "simuladas": sum(r["status"].startswith("simulada") for r in rows),
    }
    return jsonify(rows=list(reversed(rows)), stats=stats)


@app.get("/api/unanswered")
def unanswered():
    cfg = load_config()
    rules = [m.lower() for r in cfg.get("answers") or [] for m in r["match"]]
    counts = Counter(r["pergunta"] for r in read_csv(UNANSWERED_CSV))
    items = [{"pergunta": q, "vezes": n} for q, n in counts.most_common()
             if not any(m in q.lower() for m in rules)]
    return jsonify(items=items)


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    print(f"Interface em {url}  (Ctrl+C para sair)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False)
