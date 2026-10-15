# Robô de candidaturas LinkedIn (Easy Apply)

Candidata você automaticamente às vagas com **"Candidatura simplificada"** do LinkedIn,
usando seu currículo e suas respostas. Tudo roda no seu próprio computador: seu login,
currículo e dados nunca saem da sua máquina.

## Instalação (uma vez só)
Funciona no **Windows**.

1. **Instale o Python** (3.12 ou mais novo): https://www.python.org/downloads/
   Na primeira tela do instalador, **marque "Add python.exe to PATH"** antes de clicar em *Install Now*.
2. **Baixe este projeto**: no GitHub, clique no botão verde **Code → Download ZIP** e
   extraia a pasta num caminho curto, por exemplo `C:\botmateusduarte` (caminhos muito
   longos fazem a instalação falhar no Windows).
   Quem usa git pode clonar: `git clone https://github.com/mduartte/botmateusduarte.git`
3. Dentro da pasta, dê dois cliques em **`setup.bat`** e espere terminar (alguns minutos).

Seus dados (`config.yaml`, `profile/`, `resume/`, CSVs) ficam só na sua máquina e
não são versionados; o `config.yaml` é criado a partir de `config.example.yaml`.

## Como usar
Dê dois cliques em **`interface.bat`**. Abre uma janela preta (o servidor — deixe aberta)
e a interface em http://127.0.0.1:5000.

1. **Meu perfil**
   - *Conectar ao LinkedIn*: abre uma janela do Chrome para você fazer login (a sessão fica
     salva em `profile/`; a senha não é guardada).
   - *Quais dados enviar*: **Meu currículo e informações daqui** (envia o PDF e seus dados,
     substituindo o que o LinkedIn pré-preenche) ou **O que já está salvo no LinkedIn**.
   - *Currículo*: envie o PDF; nome, e-mail, telefone e LinkedIn são lidos dele automaticamente.
   - *Informações pessoais e profissionais*: usadas para preencher os formulários.
2. **Vagas**: buscas, filtros e limites. Em *Confirmar cada envio* você escolhe se o robô
   pergunta antes de cada candidatura ou envia direto.
3. **Painel**: *Testar (simulação)* percorre os formulários sem enviar; *Iniciar envios* envia de verdade.
   Comece pela simulação.
4. **Respostas**: perguntas que o robô não soube responder (vaga pulada). Responda e ela vira regra.
5. **Histórico**: todas as candidaturas (também em `applied.csv`).

Também dá para rodar sem interface: `run.bat` (usa o `config.yaml`).

## Problemas comuns
- **"Python não encontrado" no setup**: reinstale o Python marcando *Add python.exe to PATH*.
- **Erro "nome do arquivo ou extensão é muito grande" no setup**: mova a pasta para um caminho
  curto (ex.: `C:\botmateusduarte`) e rode o `setup.bat` de novo.
- **A interface não abre**: confira se a janela preta do `interface.bat` continua aberta e acesse
  http://127.0.0.1:5000 no navegador.
- **Parou de achar botões/vagas**: o LinkedIn mudou o site; baixe a versão mais nova deste projeto.

## Avisos
- Automatizar o LinkedIn vai contra os Termos de Uso dele e pode gerar restrição ou bloqueio
  da conta. Mantenha limites baixos.
- Confira suas respostas na aba *Respostas*: elas são enviadas às empresas como verdadeiras.
- O LinkedIn muda o HTML com frequência; se algo parar de funcionar, os seletores em
  `autoapply.py` precisam de ajuste.
