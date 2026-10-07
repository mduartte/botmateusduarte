"""Robô de candidaturas "Candidatura simplificada" (Easy Apply) do LinkedIn.

Uso:
    python autoapply.py            # roda com config.yaml
    python autoapply.py --login    # só abre o navegador para você fazer login

O login é feito por você, manualmente, na primeira execução; a sessão fica salva
na pasta ./profile. Nenhuma senha é guardada pelo script.
"""
import argparse
import csv
import json
import os
import random
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import yaml
from playwright.sync_api import Error as PWError
from playwright.sync_api import Locator, Page, TimeoutError as PWTimeout, sync_playwright

BASE = Path(__file__).resolve().parent
PROFILE_DIR = BASE / "profile"
APPLIED_CSV = BASE / "applied.csv"
UNANSWERED_CSV = BASE / "unanswered_questions.csv"
GUI = os.environ.get("AUTOAPPLY_GUI") == "1"

WORK_TYPE = {"presencial": "1", "remoto": "2", "hibrido": "3"}
EXP_LEVEL = {"estagio": "1", "junior": "2", "assistente": "3", "pleno_senior": "4", "diretor": "5", "executivo": "6"}

EASY_APPLY_RE = re.compile(r"candidatura simplificada|easy apply", re.I)
NEXT_RE = re.compile(r"avançar|próxima|continuar|next|continue", re.I)
REVIEW_RE = re.compile(r"revisar|review", re.I)
SUBMIT_RE = re.compile(r"enviar candidatura|submit application", re.I)
DISCARD_RE = re.compile(r"descartar|discard", re.I)
YES_RE = re.compile(r"^(sim|yes)$", re.I)
NO_RE = re.compile(r"^(não|nao|no)$", re.I)


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def append_csv(path: Path, header: list[str], row: list) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        if new:
            w.writerow(header)
        w.writerow(row)


def load_applied_ids() -> set[str]:
    if not APPLIED_CSV.exists():
        return set()
    with APPLIED_CSV.open(encoding="utf-8-sig") as f:
        return {r["job_id"] for r in csv.DictReader(f, delimiter=";") if r.get("status") == "enviada"}


# Campos do perfil do usuário → palavras que identificam a pergunta no formulário.
# "contact" = dados de contato que, no modo "local", substituem o que o LinkedIn pré-preencheu.
PROFILE_FIELDS = [
    ("first_name", ["primeiro nome", "first name", "given name"], True),
    ("last_name", ["sobrenome", "ultimo nome", "last name", "family name", "surname"], True),
    ("full_name", ["nome completo", "full name", "seu nome", "your name"], True),
    ("email", ["e-mail", "email"], True),
    ("phone", ["telefone", "celular", "phone", "mobile", "whatsapp"], True),
    ("city", ["cidade", "city", "localizacao", "location", "onde voce mora", "endereco"], True),
    ("linkedin_url", ["linkedin"], False),
    ("portfolio_url", ["portfolio", "github", "website", "site pessoal", "personal site"], False),
    ("salary", ["pretensao", "salario", "salary", "remuneracao", "compensation", "expectativa salarial"], False),
    ("english_level", ["ingles", "english"], False),
    ("availability", ["disponibilidade", "start date", "quando pode comecar", "aviso previo", "notice period"], False),
]


def build_profile(cfg: dict) -> dict:
    p = dict(cfg.get("profile") or cfg.get("personal") or {})
    full = str(p.get("full_name") or "").strip()
    if full:
        parts = full.split()
        p.setdefault("first_name", parts[0])
        p.setdefault("last_name", " ".join(parts[1:]))
    return {k: str(v).strip() for k, v in p.items() if v not in (None, "")}


class Answerer:
    def __init__(self, cfg: dict):
        self.rules = [([norm(m) for m in r["match"]], str(r["answer"])) for r in cfg.get("answers") or []]
        self.profile = build_profile(cfg)
        years = self.profile.get("years_experience")
        self.default_years = years if years is not None else cfg.get("default_years_experience")
        self.default_yes_no = cfg.get("default_yes_no")

    def profile_field(self, question: str, contact_only: bool = False) -> str | None:
        q = norm(question).rstrip(" *")
        if q in ("nome", "name") and "first_name" in self.profile:  # LinkedIn em PT: "Nome" + "Sobrenome"
            return self.profile["first_name"]
        for key, words, contact in PROFILE_FIELDS:
            if contact_only and not contact:
                continue
            if key in self.profile and any(w in q for w in words):
                return self.profile[key]
        return None

    def for_question(self, question: str) -> str | None:
        q = norm(question)
        for keys, answer in self.rules:
            if any(k in q for k in keys):
                return answer
        return self.profile_field(question)

    def numeric(self, question: str) -> str | None:
        ans = self.for_question(question)
        if ans is not None and re.fullmatch(r"\d+([.,]\d+)?", ans.strip()):
            return ans.strip()
        if self.default_years not in (None, "") and re.search(r"anos|years|experien", norm(question)):
            return str(self.default_years)
        return None

    def yes_no(self, question: str) -> str | None:
        ans = self.for_question(question)
        return ans if ans is not None else self.default_yes_no


class Bot:
    def __init__(self, page: Page, cfg: dict):
        self.page = page
        self.cfg = cfg
        self.answerer = Answerer(cfg)
        self.applied_ids = load_applied_ids()
        self.sent = 0
        self.current_job = ""
        # "local" = usa currículo e dados da aba Meu perfil; "linkedin" = usa o que já está salvo no LinkedIn
        self.use_local = cfg.get("data_source", "local") == "local"

    # ---------------------------------------------------------------- login
    def ensure_logged_in(self) -> None:
        self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        if "/feed" in self.page.url:
            log("Sessão do LinkedIn ativa.")
            return
        log("Faça login no LinkedIn na janela aberta (inclusive 2FA). Aguardando até 5 minutos...")
        self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        self.page.wait_for_url(re.compile(r"linkedin\.com/(feed|jobs|in/)"), timeout=300_000)
        log("Login detectado.")

    # --------------------------------------------------------------- search
    def search_url(self, keywords: str, location: str, start: int) -> str:
        f = self.cfg.get("filters") or {}
        params = {"keywords": keywords, "location": location, "f_AL": "true", "start": start}
        if f.get("posted_within_hours"):
            params["f_TPR"] = f"r{int(f['posted_within_hours']) * 3600}"
        if f.get("remote"):
            params["f_WT"] = ",".join(WORK_TYPE[x] for x in f["remote"])
        if f.get("experience_levels"):
            params["f_E"] = ",".join(EXP_LEVEL[x] for x in f["experience_levels"])
        return "https://www.linkedin.com/jobs/search/?" + urlencode(params)

    def run(self) -> None:
        self.ensure_logged_in()
        pages = int((self.cfg.get("filters") or {}).get("pages_per_search", 1))
        for s in self.cfg["searches"]:
            for p in range(pages):
                if self.limit_reached():
                    return
                url = self.search_url(s["keywords"], s.get("location", ""), p * 25)
                log(f"Busca: '{s['keywords']}' em '{s.get('location', '')}' — página {p + 1}")
                self.page.goto(url, wait_until="domcontentloaded")
                if not self.process_results_page():
                    break
        log(f"Fim. Candidaturas enviadas nesta execução: {self.sent}")

    def limit_reached(self) -> bool:
        if self.sent >= int(self.cfg.get("max_applications_per_run", 20)):
            log("Limite de candidaturas por execução atingido.")
            return True
        return False

    def job_cards(self) -> Locator:
        return self.page.locator("li[data-occludable-job-id]")

    def process_results_page(self) -> bool:
        try:
            self.job_cards().first.wait_for(timeout=15_000)
        except PWTimeout:
            log("Nenhuma vaga nesta página.")
            return False
        count = self.job_cards().count()
        for i in range(count):
            if self.limit_reached():
                return False
            card = self.job_cards().nth(i)
            job_id = card.get_attribute("data-occludable-job-id") or ""
            if not job_id or job_id in self.applied_ids:
                continue
            try:
                card.scroll_into_view_if_needed()
                card.locator("a").first.click()
                self.page.wait_for_timeout(1500)
                self.handle_job(job_id)
            except (PWTimeout, PWError) as e:
                log(f"  Erro na vaga {job_id}: {str(e).splitlines()[0]}")
                self.close_modal(discard=True)
        return True

    # ------------------------------------------------------------------ job
    def detail_text(self, selector: str) -> str:
        loc = self.page.locator(selector).first
        try:
            return loc.inner_text(timeout=3000).strip()
        except (PWTimeout, PWError):
            return ""

    def handle_job(self, job_id: str) -> None:
        title = self.detail_text(".job-details-jobs-unified-top-card__job-title, .jobs-unified-top-card__job-title, h1")
        company = self.detail_text(".job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name")
        label = f"{title} @ {company}"
        if any(norm(w) in norm(title) for w in self.cfg.get("blacklist_title_words") or []):
            log(f"  Pulada (título bloqueado): {label}")
            return
        if any(norm(c) == norm(company) for c in self.cfg.get("blacklist_companies") or []):
            log(f"  Pulada (empresa bloqueada): {label}")
            return

        button = self.page.locator("button.jobs-apply-button, .jobs-apply-button--top-card button").filter(has_text=EASY_APPLY_RE).first
        if not button.is_visible():
            log(f"  Pulada (já candidatado ou sem Easy Apply): {label}")
            return

        log(f"Vaga: {label}")
        self.current_job = label
        button.click()
        modal = self.page.locator("div.jobs-easy-apply-modal, div[role='dialog']").first
        modal.wait_for(timeout=10_000)

        status, detail = self.fill_and_submit(modal)
        log(f"  -> {status}{': ' + detail if detail else ''}")
        append_csv(APPLIED_CSV, ["data", "job_id", "titulo", "empresa", "status", "detalhe", "url"],
                   [datetime.now().isoformat(timespec="seconds"), job_id, title, company, status, detail,
                    f"https://www.linkedin.com/jobs/view/{job_id}/"])
        if status == "enviada":
            self.applied_ids.add(job_id)
            self.sent += 1
            lo, hi = self.cfg.get("delay_between_applications_seconds", [10, 25])
            time.sleep(random.uniform(lo, hi))

    # ----------------------------------------------------------------- form
    def fill_and_submit(self, modal: Locator) -> tuple[str, str]:
        last_progress = None
        stuck = 0
        for _ in range(20):
            self.page.wait_for_timeout(800)
            unanswered = self.fill_step(modal)

            footer_buttons = modal.locator("footer button, .jobs-easy-apply-modal__footer button, div.display-flex button.artdeco-button--primary")
            submit = footer_buttons.filter(has_text=SUBMIT_RE).first
            if submit.count() and submit.is_visible():
                if unanswered:
                    self.log_unanswered(unanswered)
                    self.close_modal(discard=True)
                    return "pulada", "perguntas sem resposta: " + " | ".join(unanswered)
                if self.cfg.get("unfollow_company", True):
                    self.uncheck_follow(modal)
                if self.cfg.get("dry_run"):
                    self.close_modal(discard=True)
                    return "simulada (dry_run)", ""
                if self.cfg.get("confirm_before_submit", True):
                    ans = self.ask_confirm()
                    if ans == "q":
                        self.close_modal(discard=True)
                        sys.exit(0)
                    if ans != "s":
                        self.close_modal(discard=True)
                        return "cancelada pelo usuário", ""
                submit.click()
                self.page.wait_for_timeout(2500)
                self.close_modal(discard=False)
                return "enviada", ""

            nxt = footer_buttons.filter(has_text=REVIEW_RE).first
            if not (nxt.count() and nxt.is_visible()):
                nxt = footer_buttons.filter(has_text=NEXT_RE).first
            if not (nxt.count() and nxt.is_visible()):
                self.close_modal(discard=True)
                return "pulada", "botão de avançar não encontrado"

            if unanswered:
                self.log_unanswered(unanswered)
                self.close_modal(discard=True)
                return "pulada", "perguntas sem resposta: " + " | ".join(unanswered)

            nxt.click()
            self.page.wait_for_timeout(1200)

            progress = self.progress(modal)
            errors = modal.locator(".artdeco-inline-feedback--error:visible")
            if errors.count() or progress == last_progress:
                stuck += 1
                if stuck >= 2:
                    msgs = [norm(e) for e in errors.all_inner_texts()][:3]
                    self.close_modal(discard=True)
                    return "pulada", "formulário com erro de validação " + "; ".join(msgs)
            else:
                stuck = 0
            last_progress = progress
        self.close_modal(discard=True)
        return "pulada", "formulário longo demais"

    def ask_confirm(self) -> str:
        if GUI:
            # A interface web lê esta linha, mostra um diálogo e responde pelo stdin.
            print("@@CONFIRM@@" + json.dumps({"job": self.current_job}, ensure_ascii=False), flush=True)
            return (sys.stdin.readline() or "q").strip().lower()
        return input("  Enviar esta candidatura? [s/N/q=sair] ").strip().lower()

    def progress(self, modal: Locator) -> str:
        try:
            return modal.locator("progress, [role='progressbar']").first.get_attribute("value", timeout=1000) or ""
        except (PWTimeout, PWError):
            return modal.inner_text()[:200]

    def question_text(self, el: Locator) -> str:
        """Texto da pergunta associada a um campo (label[for], aria-label ou contêiner)."""
        el_id = el.get_attribute("id")
        if el_id:
            lab = el.page.locator(f"label[for='{el_id}']")
            if lab.count():
                return lab.first.inner_text().strip()
        aria = el.get_attribute("aria-label")
        if aria:
            return aria.strip()
        container = el.locator("xpath=ancestor::*[contains(@class,'form-element') or contains(@class,'grouping')][1]")
        if container.count():
            return container.first.inner_text().split("\n")[0].strip()
        return ""

    def fill_step(self, modal: Locator) -> list[str]:
        unanswered: list[str] = []
        self.choose_resume(modal)

        # Campos de texto / número / textarea
        for el in modal.locator("input[type='text']:visible, input[type='tel']:visible, input[type='number']:visible, "
                                "input:not([type]):visible, textarea:visible").all():
            current = el.input_value().strip()
            if current:
                # Modo local: troca contato pré-preenchido pelo LinkedIn pelos dados do perfil
                mine = self.answerer.profile_field(self.question_text(el), contact_only=True) if self.use_local else None
                if mine and norm(mine) != norm(current):
                    el.fill(mine)
                    self.pick_autocomplete(el)
                continue
            q = self.question_text(el)
            is_numeric = (el.get_attribute("type") == "number"
                          or "numeric" in (el.get_attribute("id") or "")
                          or bool(re.search(r"quantos anos|how many years|anos de experiencia|years of experience", norm(q))))
            ans = self.answerer.numeric(q) if is_numeric else self.answerer.for_question(q)
            if ans is None:
                if el.get_attribute("required") is not None or el.get_attribute("aria-required") == "true":
                    unanswered.append(q or "(campo de texto sem rótulo)")
                continue
            el.fill(ans)
            self.pick_autocomplete(el)

        # Selects
        for sel in modal.locator("select:visible").all():
            current = norm(sel.locator("option:checked").inner_text() if sel.locator("option:checked").count() else "")
            q = self.question_text(sel)
            options = [o for o in sel.locator("option").all_inner_texts() if not re.search(r"selecion|select an option", norm(o))]
            if current and not re.search(r"selecion|select an option", current):
                mine = self.answerer.profile_field(q, contact_only=True) if self.use_local else None
                match = self.pick_option(mine, options) if mine else None
                if match and norm(match) != current:
                    sel.select_option(label=match)  # ex.: e-mail do perfil em vez do e-mail da conta
                continue
            choice = self.pick_option(self.answerer.yes_no(q), options)
            if choice is None:
                unanswered.append(q or "(lista sem rótulo)")
                continue
            sel.select_option(label=choice)

        # Grupos de rádio (Sim/Não e múltipla escolha)
        for fs in modal.locator("fieldset:visible").all():
            radios = fs.locator("input[type='radio']")
            if not radios.count() or fs.locator("input[type='radio']:checked").count():
                continue
            legend = fs.locator("legend, span[data-test-form-builder-radio-button-form-component__title]").first
            q = legend.inner_text().strip() if legend.count() else fs.inner_text().split("\n")[0]
            labels = [fs.locator(f"label[for='{r.get_attribute('id')}']").first for r in radios.all()]
            texts = [l.inner_text().strip() for l in labels]
            choice = self.pick_option(self.answerer.yes_no(q), texts)
            if choice is None:
                unanswered.append(q)
                continue
            labels[texts.index(choice)].click()

        # Checkboxes obrigatórias (ex.: "Concordo com os termos")
        for cb in modal.locator("fieldset:visible input[type='checkbox'][required], input[type='checkbox'][aria-required='true']").all():
            if not cb.is_checked():
                cb_id = cb.get_attribute("id")
                modal.locator(f"label[for='{cb_id}']").first.click()
        return unanswered

    @staticmethod
    def pick_option(answer: str | None, options: list[str]) -> str | None:
        if answer is None or not options:
            return None
        a = norm(answer)
        for o in options:
            if norm(o) == a:
                return o
        if YES_RE.match(a):
            return next((o for o in options if YES_RE.match(norm(o))), None)
        if NO_RE.match(a) or a == "nao":
            return next((o for o in options if NO_RE.match(norm(o))), None)
        return next((o for o in options if a in norm(o) or norm(o) in a), None)

    def pick_autocomplete(self, el: Locator) -> None:
        if el.get_attribute("role") == "combobox" or el.get_attribute("aria-autocomplete"):
            self.page.wait_for_timeout(1200)
            option = self.page.locator("[role='listbox'] [role='option']:visible").first
            if option.count():
                option.click()

    def choose_resume(self, modal: Locator) -> None:
        """Seleciona o currículo na etapa de documentos.

        Modo local: usa o PDF da aba Meu perfil (seleciona se já foi enviado antes, senão faz upload).
        Modo linkedin: mantém o currículo que o LinkedIn já selecionou ou escolhe o mais recente salvo lá.
        """
        file_input = modal.locator("input[type='file']")
        if not file_input.count():
            return
        selected = modal.locator("[class*='document'][class*='selected'], [aria-checked='true'][class*='document'], "
                                 "input[type='radio'][id*='resume']:checked, input[type='radio'][id*='jobsDocument']:checked")
        if not self.use_local:
            if not selected.count():
                saved = modal.locator("[class*='document-upload'] label, [class*='jobs-resume-picker'] label, "
                                      "input[type='radio'][id*='jobsDocument'] + label").first
                if saved.count():
                    saved.click()
            return

        path = Path(self.cfg.get("resume_path") or "")
        if not path.is_file():
            log(f"  Aviso: currículo não encontrado em '{path}'. Envie o PDF na aba Meu perfil.")
            return
        # O LinkedIn mostra os currículos já enviados pelo nome do arquivo
        existing = modal.get_by_text(path.name, exact=False).first
        if existing.count() and existing.is_visible():
            existing.click()
            return
        file_input.first.set_input_files(str(path))
        self.page.wait_for_timeout(3000)

    def uncheck_follow(self, modal: Locator) -> None:
        cb = modal.locator("input#follow-company-checkbox, input[type='checkbox'][id*='follow']").first
        try:
            if cb.count() and cb.is_checked():
                modal.locator(f"label[for='{cb.get_attribute('id')}']").first.click()
        except PWError:
            pass

    def close_modal(self, discard: bool) -> None:
        try:
            close = self.page.locator("div[role='dialog'] button[aria-label*='Dismiss'], "
                                      "div[role='dialog'] button[aria-label*='Fechar'], "
                                      "div[role='dialog'] button[data-test-modal-close-btn]").first
            if close.count() and close.is_visible():
                close.click()
                self.page.wait_for_timeout(800)
            confirm = self.page.locator("div[role='alertdialog'] button, div[role='dialog'] button").filter(has_text=DISCARD_RE).first
            if discard and confirm.count() and confirm.is_visible():
                confirm.click()
                self.page.wait_for_timeout(800)
        except PWError:
            pass

    def log_unanswered(self, questions: list[str]) -> None:
        for q in questions:
            append_csv(UNANSWERED_CSV, ["data", "pergunta"], [datetime.now().isoformat(timespec="seconds"), q])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(BASE / "config.yaml"))
    parser.add_argument("--login", action="store_true", help="só abre o navegador para login")
    parser.add_argument("--dry-run", action="store_true", help="simula sem enviar (sobrepõe o config)")
    args = parser.parse_args()

    if not Path(args.config).exists():
        shutil.copy(BASE / "config.example.yaml", args.config)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    if args.dry_run:
        cfg["dry_run"] = True
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=False, viewport={"width": 1366, "height": 900},
                                                   locale="pt-BR")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        bot = Bot(page, cfg)
        try:
            if args.login:
                bot.ensure_logged_in()
                if GUI:
                    log("Login salvo. Pode fechar.")
                    page.wait_for_timeout(3000)
                else:
                    input("Logado. Pressione Enter para fechar...")
            else:
                bot.run()
        except KeyboardInterrupt:
            log("Interrompido pelo usuário.")
        finally:
            ctx.close()


if __name__ == "__main__":
    main()
