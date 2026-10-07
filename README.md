# Robô de candidaturas LinkedIn (Easy Apply)

## Instalação
Requer Windows e Python 3.12+. Depois de clonar, rode **`setup.bat`** uma vez.
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
2. **Vagas**: buscas, filtros e limites.
3. **Painel**: *Testar (simulação)* percorre os formulários sem enviar; *Iniciar envios* envia de verdade.
4. **Respostas**: perguntas que o robô não soube responder (vaga pulada). Responda e ela vira regra.
5. **Histórico**: todas as candidaturas (também em `applied.csv`).

Também dá para rodar sem interface: `run.bat` (usa o `config.yaml`).

## Avisos
- Automatizar o LinkedIn vai contra os Termos de Uso dele e pode gerar restrição ou bloqueio
  da conta. Mantenha limites baixos.
- O LinkedIn muda o HTML com frequência; se algo parar de funcionar, os seletores em
  `autoapply.py` precisam de ajuste.
