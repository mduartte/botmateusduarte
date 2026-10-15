# CLAUDE.md

Robô que se candidata automaticamente a vagas "Candidatura simplificada" (Easy Apply) do LinkedIn,
com interface web local. Windows, Python 3.12, Playwright (Chromium), Flask. UI e textos em português.

## Comandos
- Instalar (após clonar): `setup.bat` (cria `.venv`, instala `requirements.txt` e o Chromium do Playwright)
- Interface: `interface.bat` → servidor Flask em http://127.0.0.1:5000 (abre o navegador sozinho)
- Robô sem interface: `run.bat` | `run.bat --login` | `.venv\Scripts\python.exe autoapply.py --dry-run`
- Checar sintaxe: `.venv\Scripts\python.exe -m py_compile app.py autoapply.py`
- Não há suíte de testes. Para testar a API sem tocar nos dados reais, use `app.app.test_client()`
  e redirecione `app.CONFIG`, `app.RESUME_DIR` e `app.PROFILE_DIR` para uma pasta temporária.
  Não há como testar o fluxo no LinkedIn sem login real do usuário — diga isso ao relatar.

## Arquitetura
- `autoapply.py` — o robô. `Bot` navega (busca → cartão da vaga → modal Easy Apply → etapas do
  formulário); `Answerer` decide respostas. Ordem de prioridade: regras `answers` do config →
  campos do `profile` (`PROFILE_FIELDS`) → `default_yes_no` / `years_experience`.
  Se uma pergunta obrigatória não tem resposta, a vaga é **pulada** e a pergunta vai para
  `unanswered_questions.csv` — o robô nunca inventa respostas. Perguntas opcionais sem resposta
  ficam em branco (`left_blank`); se o LinkedIn recusar por validação, também vão para o CSV.
  Obrigatória = `required`/`aria-required` ou "Obrigatório"/"Required" no rótulo (`is_required`).
- A lista de vagas carrega aos poucos: `load_all_cards` rola o contêiner até o fim antes de iterar.
  Se o navegador fecha, `main()` reabre (até 2x) e retoma da mesma busca/página (`Bot.position`).
- `app.py` — Flask. `Runner` executa `autoapply.py` como subprocesso (`AUTOAPPLY_GUI=1`) e lê o stdout.
  Protocolo de confirmação: o robô imprime `@@CONFIRM@@{json}` e lê `s`/`n`/`q` do stdin.
  Login detectado lendo o cookie `li_at` no SQLite de cookies em `profile/` (copiado antes, pois fica travado).
  Parar usa `taskkill /T` para matar também o navegador.
- `web/index.html` — front-end em arquivo único (HTML/CSS/JS puro, sem build, sem CDN).
  Abas: Painel, Meu perfil, Vagas, Respostas, Histórico. Tokens de cor em `:root` com modo escuro.
- `config.yaml` — fonte única de configuração, lida pelo robô e escrita pela UI (`yaml.safe_dump`,
  perde comentários). `data_source`: `local` (usa PDF + `profile` e sobrescreve contatos que o
  LinkedIn pré-preenche) ou `linkedin` (usa currículo/dados salvos na conta).

## Regras
- **Nunca versionar dados pessoais**: `profile/` (cookies da sessão LinkedIn), `resume/`, `config.yaml`,
  `*.csv` já estão no `.gitignore`. Mudanças de esquema do config vão em `config.example.yaml`
  (o `config.yaml` é criado a partir dele quando não existe) e precisam ser refletidas em
  `collectConfig`/`renderConfig` do `web/index.html`.
- Não colocar respostas fictícias como padrão no config (salário, "Sim" genéricos etc.): seriam
  enviadas às empresas como verdadeiras.
- Seletores do LinkedIn mudam com frequência. Prefira texto/aria/regex PT+EN (`EASY_APPLY_RE`,
  `NEXT_RE`, `SUBMIT_RE`…) a classes CSS; ao mexer, mantenha fallbacks e a comparação via `norm()`
  (sem acentos, minúsculas).
- Não adicionar burla de detecção/CAPTCHA nem login automático com senha. Mantenha os limites
  (`max_applications_per_run`, atrasos) — automação viola os Termos do LinkedIn e arrisca a conta do usuário.
- Logs do robô via `log()`; o front colore linhas por padrões (`-> enviada`, `-> pulada`, `Vaga:`…) em
  `logClass` — ao mudar mensagens, ajuste lá também.
- Shell é Windows PowerShell 5.1; arquivos CSV usam `;` e `utf-8-sig` (abrem certo no Excel).
