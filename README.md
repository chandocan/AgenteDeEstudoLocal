# AgenteDeEstudoLocal --- documentação revisada

**Ambiente:** Ubuntu 24.04, Python 3.12, MySQL 8\
**Diretório:** `/home/dog_alfredo/AgenteDeEstudoLocal`

## 1. Objetivo e arquitetura

O projeto mantém uma agenda de estudos no MySQL. O servidor controla
estudos, datas, prazos, notificações e histórico. Em uma etapa futura, o
computador local utilizará OpenCode + Ollama para produzir o conteúdo
das aulas quando houver tarefas pendentes.

Há três estados de geração independentes do estado do estudo: -
**aguardando:** a data de geração ainda não chegou. - **gerando:** a data
de geração chegou e o conteúdo está pendente de produção pelo agente
local (OpenCode + Ollama). - **gerado:** o conteúdo foi produzido (a
partir da etapa de integração do agente).

E o estado do estudo: - **Estudo pendente:** o usuário ainda não
confirmou que estudou o conteúdo.

## 2. Estrutura atual

``` text
AgenteDeEstudoLocal/
├── .venv/
├── agenda_estudo.py
├── agenda_estudos.db
├── agenda_estudos.db-shm
├── agenda_estudos.db-wal
├── agenda_estudos.log
└── agenda_estudos_sqlite_backup.py
```

O script atual é `agenda_estudo.py` (singular) e utiliza MySQL. Os
arquivos SQLite antigos foram preservados; não foi confirmada migração
dos dados SQLite para MySQL.

## 3. Ambiente Python

Entrar na pasta e ativar o ambiente virtual:

``` bash
cd ~/AgenteDeEstudoLocal
source .venv/bin/activate
```

Para sair do ambiente virtual:

``` bash
deactivate
```

O conector `mysql-connector-python` está instalado no `.venv`.

## 4. Configuração do banco

Arquivo de configuração:

``` text
/etc/agenda-estudos.conf
```

Variáveis esperadas:

``` ini
AGENDA_DB_HOST=localhost
AGENDA_DB_PORT=3306
AGENDA_DB_NAME=agenda_estudos
AGENDA_DB_USER=agenda_app
AGENDA_DB_PASSWORD=SENHA_DO_USUARIO_MYSQL
```

Não coloque a senha real nesta documentação, em comandos compartilhados
ou em logs.

Permissões configuradas para permitir leitura ao root e ao grupo
`dog_alfredo`:

``` bash
sudo chown root:dog_alfredo /etc/agenda-estudos.conf
sudo chmod 640 /etc/agenda-estudos.conf
```

Para carregar as variáveis no terminal atual antes de executar comandos
manuais:

``` bash
set -a
source /etc/agenda-estudos.conf
set +a
```

Esse carregamento vale para a sessão atual. Em um novo terminal, repita
os comandos. O serviço systemd carrega o arquivo automaticamente por
meio de `EnvironmentFile`.

Banco ativo: `agenda_estudos`\
Usuário da aplicação: `agenda_app@localhost`

O script atual utiliza as tabelas: - `estudos` - `notificacoes` -
`historico_estudos`

Não utilize o usuário root do MySQL na aplicação.

## 5. Ajuda e comandos disponíveis

Ver todos os subcomandos:

``` bash
python agenda_estudo.py --help
```

Subcomandos identificados:

``` text
adicionar
listar
concluir
ver
executar
processar
eventos
notificacoes
```

### 5.2.1 Ver/exportar a aula gerada

``` bash
python agenda_estudo.py ver --id 4
python agenda_estudo.py ver --id 4 --exportar aula4.md
```

Exibe o conteúdo gerado no terminal ou exporta para arquivo Markdown.
Quando a aula ainda não foi gerada, informa o status atual.

### 5.1 Listar estudos

``` bash
python agenda_estudo.py listar
```

Exibe ID, tema, tópicos, datas, status da geração e status do estudo.

### 5.2 Adicionar estudo

Formato:

``` bash
python agenda_estudo.py adicionar \
  --tema "TEMA DO ESTUDO" \
  --topicos "TÓPICO 1, tópico 2, tópico 3" \
  --gerar-em "AAAA-MM-DD HH:MM" \
  --prazo-em "AAAA-MM-DD HH:MM"
```

  Parâmetro        Obrigatório? Função
  -------------- -------------- -----------------------------------------------
  `--tema`                  Sim Tema principal do estudo
  `--topicos`               Não Tópicos que deverão ser abordados
  `--gerar-em`              Sim Data e hora programadas para gerar o conteúdo
  `--prazo-em`              Sim Data e hora limite para concluir o estudo

**Formato de data:** `AAAA-MM-DD HH:MM`. Não informe segundos.

Exemplo de cadastro já executado com sucesso:

``` bash
python agenda_estudo.py adicionar \
  --tema "Teste do Agendador" \
  --topicos "Introdução, conceitos básicos e revisão" \
  --gerar-em "2026-10-31 08:00" \
  --prazo-em "2026-11-02 18:00"
```

Retorno observado:

``` text
Estudo cadastrado com ID 1.
```

### 5.3 Consultar notificações

``` bash
python agenda_estudo.py notificacoes
```

Consulta os registros de notificações no banco. A mensagem
`Nenhuma notificação registrada.` significa que a consulta funcionou,
mas não há registros.

### 5.4 Consultar eventos do histórico

``` bash
python agenda_estudo.py eventos
```

Exibe eventos registrados pelo sistema. O cadastro de um estudo, por
exemplo, gerou:

``` text
[2026-10-30 12:45:00] estudo=1 cadastro: Estudo cadastrado: Teste do Agendador
```

### 5.5 Concluir e reabrir um estudo

A conclusão é um eixo **independente** da geração e da entrega: pode
ser registrada a qualquer momento e guarda o contexto do instante.

``` bash
python agenda_estudo.py concluir --id 3
python agenda_estudo.py concluir --id 3 \
  --data "2026-10-30 17:00" \
  --observacao "dificuldade em X; repassei o tópico Y"
python agenda_estudo.py reabrir --id 3   # desfaz a conclusão
```

- `--data`: registra quando de fato o estudo foi feito (retroativo);
  padrão: agora.
- `--observacao`: texto livre para o histórico.
- A conclusion bloqueia duplicatas (usa `reabrir` antes de concluir
  de novo) e envia e-mail de confirmação
  (`estudo_concluido`), registrando no histórico o status de geração e
  o registro de entrega (`entregue_em`) no momento da conclusão.

Migração única (coluna `entregue_em` em `estudos`, pois o usuário
`agenda_app` não tem `ALTER`):

``` bash
sudo mysql agenda_estudos \
  -e "ALTER TABLE estudos ADD COLUMN entregue_em DATETIME NULL;"
```

O envio do e-mail "aula pronta" marca `entregue_em` (uma única vez).
`listar` exibe entrega e conclusão quando existentes.

### 5.6 Executar o agendador manualmente

``` bash
python agenda_estudo.py executar
```

Esse comando inicia o processo do agendador. Quando o serviço systemd
estiver ativo, evite iniciar uma segunda instância manual.

### 5.7 Executar um único ciclo de verificação

``` bash
python agenda_estudo.py processar
```

Executa um ciclo de `verificar_agenda` e encerra. Útil para testes e
diagnóstico sem interferir no loop contínuo do serviço. Na execução de
30/10/2026, o estudo ID 2 (geração programada no passado) passou de
`aguardando` para `gerando`, com lembrete, notificação e histórico
registrados em um único ciclo.

## 6. Serviço systemd

Arquivo da unidade:

``` text
/etc/systemd/system/agenda-estudos.service
```

O serviço usa o Python do ambiente virtual e carrega as variáveis de
`/etc/agenda-estudos.conf`.

Consultar estado:

``` bash
sudo systemctl status agenda-estudos.service
```

Iniciar:

``` bash
sudo systemctl start agenda-estudos.service
```

Parar:

``` bash
sudo systemctl stop agenda-estudos.service
```

Reiniciar:

``` bash
sudo systemctl restart agenda-estudos.service
```

Ver as últimas 50 linhas do log:

``` bash
sudo journalctl -u agenda-estudos.service -n 50 --no-pager
```

Acompanhar log em tempo real:

``` bash
sudo journalctl -u agenda-estudos.service -f
```

Verificar se inicia automaticamente:

``` bash
sudo systemctl is-enabled agenda-estudos.service
```

Habilitar inicialização automática:

``` bash
sudo systemctl enable agenda-estudos.service
```

Após alterar o arquivo da unidade, recarregar as configurações:

``` bash
sudo systemctl daemon-reload
```

O serviço foi confirmado como `active (running)`, conectando ao banco
`agenda_estudos`.

A API REST (etapa 2) tem sua própria unidade; ver a seção 7.

## 7. API REST (etapa 2 — comunicação servidor ↔ agente local)

Arquivo do servidor: `api_agenda.py` — usa FastAPI + uvicorn (instalados
no `.venv`). Reutiliza as variáveis `AGENDA_DB_*` de
`/etc/agenda-estudos.conf` e as funções de banco de `agenda_estudo.py`.

Escuta por padrão em:

``` text
http://127.0.0.1:8800
```

Variáveis adicionais (opcionais): - `AGENDA_API_HOST` — padrão
`127.0.0.1` - `AGENDA_API_PORT` — padrão `8800` -
`AGENDA_API_TOKEN` — se definido, exige cabeçalho `X-API-Token`.

Fluxo de geração do estudo:

``` text
aguardando -> gerando -> em_producao -> gerado
                                     `- falha: volta para gerando
```

O agendador move `aguardando -> gerando`. O agente usa:
`gerando -> em_producao -> gerado` (ou volta para `gerando` em falha).

### 7.1 Endpoints

| Método | Caminho | Função |
|---|---|---|
| GET | `/api/saude` | Verificação de saúde e conexão com o banco |
| GET | `/api/tarefas/pendentes` | Lista estudos em `gerando` |
| GET | `/api/estudos?status=` | Lista estudos (filtro opcional por status de geração) |
| POST | `/api/tarefas/reivindicar` | Reivindica tarefas: `em_producao`; usa `FOR UPDATE SKIP LOCKED` |
| POST | `/api/tarefas/{id}/conteudo` | Recebe o conteúdo e marca `gerado` (grava em `conteudos`) |
| POST | `/api/tarefas/{id}/falha` | Devolve a tarefa para `gerando` para nova tentativa |

Para testes manuais do servidor, carregar as variáveis e executar:

``` bash
set -a
source /etc/agenda-estudos.conf
set +a
python api_agenda.py
```

### 7.2 Cliente de teste

`cliente_agente_teste.py` (só biblioteca padrão) simula o agente sem
Ollama/OpenCode:

``` bash
python cliente_agente_teste.py saude
python cliente_agente_teste.py pendentes
python cliente_agente_teste.py reivindicar --agente "teste"
python cliente_agente_teste.py receber --id 2
python cliente_agente_teste.py falha --id 2 --motivo "x"
```

### 7.3 Unidade systemd da API

Arquivo preparado no projeto:
`/home/dog_alfredo/AgenteDeEstudoLocal/agenda-estudos-api.service`
(`/etc/systemd/system/agenda-estudos-api.service` após instalação).

``` bash
sudo cp /home/dog_alfredo/AgenteDeEstudoLocal/agenda-estudos-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agenda-estudos-api.service
sudo journalctl -u agenda-estudos-api.service -n 50 --no-pager
```

### 7.4 Testes da etapa 2 (30/10/2026)

| Teste | Resultado |
|---|---|
| `GET /api/saude` | `ok`, conecta em `agenda_estudos` |
| `GET /api/tarefas/pendentes` | Estudo ID 2 listado |
| `POST reivindicar` (ID 2) | Status `em_producao`, histórico registrado |
| `POST .../conteudo` (ID 2) | Status `gerado`; conteúdo salvo em `conteudos` (#1, 301 caracteres) |
| Caminho de erro (ID 3) | `reivindicar` → `falha` devolveu para `gerando`; depois `receber` → `gerado` (`conteudos` #2) |

## 8. Trabalhador do agente (etapa 3 — Ollama)

Arquivo: `agente_trabalhador.py`. Em loop, consulta a API, reivindica
tarefas, gera a aula no Ollama local e entrega via API. Em caso de
erro, reporta `/falha` (a tarefa volta para `gerando`). Na
inicialização, devolve para `gerando` qualquer estudo preso em
`em_producao` (assumindo um único trabalhador).

Modelo padrão: `qwen3.5:4b` (testado em 30/10/2026: aula de ~8.5 mil
caracteres em ~8 minutos em CPU). Modelos locais disponíveis:
`qwen3.5:4b`, `llama3.2:3b`, `qwen2.5-coder:3b`.

Variáveis (todas opcionais):

``` ini
AGENDA_MODELO=qwen3.5:4b
AGENDA_OLLAMA_URL=http://127.0.0.1:11434
AGENDA_AGENTE_INTERVALO=30
AGENDA_AGENTE_TIMEOUT=600
AGENDA_AGENTE_NOME=agente-local
```

Teste manual one-shot (gera o que estiver pendente e encerra):

``` bash
set -a
source /etc/agenda-estudos.conf
set +a
python agente_trabalhador.py --uma-vez
```

Unidade systemd: `agenda-agente.service` (iniciar após
`ollama.service` e `agenda-estudos-api.service`).

``` bash
sudo cp /home/dog_alfredo/AgenteDeEstudoLocal/agenda-agente.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agenda-agente.service
sudo journalctl -u agenda-agente.service -f
```

## 9. Envio real de e-mails (Gmail)

Arquivo: `envia_emails.py` (só biblioteca padrão: `smtplib`). Consome
notificações `pendente` do canal `email`, envia por SMTP e marca
`enviado` com `enviado_em`. Em falha incrementa `tentativas`; após o
limite, marca `erro` com o detalhe. Na inicialização devolve para
`pendente` o que ficar preso em `enviando`.

O usuário `agenda_app` não tem `ALTER`; a migração única (colunas
`tentativas` e `erro` em `notificacoes`) foi executada manualmente como
root do MySQL:

``` bash
sudo mysql agenda_estudos \
  -e "ALTER TABLE notificacoes ADD COLUMN tentativas INT NOT NULL DEFAULT 0, ADD COLUMN erro TEXT NULL;"
```

Gmail exige senha de app (verificação em 2 etapas ativada):
https://myaccount.google.com/apppasswords — não registrar a senha nesta
documentação.

Variáveis em `/etc/agenda-estudos.conf`:

``` ini
AGENDA_SMTP_HOST=smtp.gmail.com
AGENDA_SMTP_PORT=587
AGENDA_SMTP_USER=EMAIL@gmail.com
AGENDA_SMTP_PASSWORD=SENHA_DE_APP
AGENDA_EMAIL_DE=EMAIL@gmail.com
AGENDA_EMAIL_PARA=EMAIL@gmail.com
```

Opcionais: `AGENDA_EMAIL_INTERVALO` (prefresh padrão 60 s),
`AGENDA_EMAIL_TENTATIVAS` (padrão 5), `AGENDA_EMAIL_LOTE` (padrão 20).

Teste manual one-shot (envia o pendente e encerra):

``` bash
set -a
source /etc/agenda-estudos.conf
set +a
python envia_emails.py --uma-vez
```

Unidade systemd: `agenda-emails.service`.

``` bash
sudo cp /home/dog_alfredo/AgenteDeEstudoLocal/agenda-emails.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agenda-emails.service
sudo journalctl -u agenda-emails.service -f
```

**Validação real (30/10/2026, 17:36, `--uma-vez`):** as 6 notificações
pendentes (lembretes de geração e "aula pronta" dos estudos #2, #3 e #4)
foram enviadas para `chandocan@gmail.com` e marcadas como `enviado` com
`enviado_em` no banco. Remetente = destinatário (mesma conta Gmail, com
senha de app). A primeira tentativa (17:32) falhou por um bug de
referência no script (corrigido); o mecanismo de recuperação devolveu o
lote para `pendente` e nenhuma notificação foi perdida — confirmado no
contador `tentativas=2` das linhas enviadas ao final.

**Entrega da aula no e-mail (30/10/2026, 18:08):** o e-mail de
"aula pronta" passou a embutir o conteúdo gerado — texto simples e,
quando disponível, versão HTML convertida do Markdown (biblioteca
`markdown` instalada no `.venv`). Validado com envio real para o
estudo #4 (notificação #7). Consulta/exportação pelo terminal via
subcomando `ver` (seção 5.2.1).

## 10. Testes já realizados

  Teste                         Resultado
  ----------------------------- ---------------------------------------------
  Ajuda geral `--help`          Comandos listados
  Ajuda de `adicionar`          Parâmetros exibidos
  Serviço systemd               `active (running)`
  `listar` antes do cadastro    Consulta OK; nenhum estudo encontrado
  `notificacoes`                Consulta OK; nenhuma notificação registrada
  `eventos` antes do cadastro   Consulta OK; nenhum evento registrado
  Cadastro de estudo            ID 1 criado
  `listar` após cadastro        Estudo recuperado do MySQL
  `eventos` após cadastro       Evento de cadastro encontrado

Estudo de teste cadastrado: - **ID:** 1 - **Tema:** Teste do Agendador -
**Tópicos:** Introdução, conceitos básicos e revisão - **Geração:**
31/10/2026 às 08:00 - **Prazo:** 02/11/2026 às 18:00 - **Status da
geração:** aguardando - **Status do estudo:** pendente

## 11. O que ainda falta validar ou implementar

Os testes confirmaram conexão com MySQL, cadastro, consultas, histórico e
o processamento da data de geração.

**Etapa 1 concluída em 30/10/2026:** quando `gerar_em` chega, o estudo
sai de `aguardando` e passa para `gerando`, com lembrete, notificação e
entrada no histórico. Validado com o estudo ID 2 via `processar`. O
estudo ID 1 serve como validação automática (serviço systemd) em
31/10/2026 às 08:00. Após validar o ID 1, marcá-lo em `gerando`.

Próximas etapas: 1. Validar o ciclo automático completo com os serviços
(estudo ID 1, 31/10/2026 às 08:00: agendador → API → Ollama → aula no
banco → e-mail com aula embutida → `entregue_em`; exige os quatro
serviços ativos). Nota (30/10/2026): **pipeline completo:** comunicação
(API, seção 7), integração com Ollama (seção 8), e-mails com aula
embutida (seção 9), consulta/exportação (`ver`, 5.2.1) e conclusão
independente do estudo com reabertura (`concluir`/`reabrir`, 5.5) —
todos implementados e testados. Estudo #3 validou conclusão com
conteúdo (`--data` retroativo e `--observacao`), recusa de duplicata,
e-mail "Estudo concluído" enviado automaticamente pelo serviço
(notificação #8) e reabertura.

**Importante:** não apagar os arquivos SQLite antigos nem presumir que
seus dados foram migrados. O banco ativo da versão atual é o MySQL
`agenda_estudos`.
