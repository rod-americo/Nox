# Nox — WADO/DICOM Assistant v2.1.0

**Nox** é um orquestrador leve e moderno para download e gerenciamento de exames DICOM via protocolo WADO. Ele atua como um *middleware* inteligente entre o RIS/PACS e o visualizador de imagens (RadiAnt, OsiriX, Horos), garantindo que os exames estejam prontos para visualização sem intervenção manual.

---

## 🚀 Funcionalidades Principais

### Gerenciamento de Downloads
*   **Múltiplos Servidores**: Suporte nativo a HBR e HAC.
*   **Alta Performance**: Downloads paralelos (multithreaded) para maximizar a banda.
*   **Retry Inteligente**: Tenta recuperar falhas de download e muda de servidor automaticamente se necessário.
*   **Contador de Sessão**: Monitora quantos exames foram baixados na sessão atual.

### Modos de Armazenamento (`Storage Mode`)
*   **Persistent (Padrão/Windows)**: Mantém os arquivos DICOM em pastas locais até atingir o limite (`max_exames`). Ideal para RadiAnt.
*   **Transient (macOS/OsiriX)**: Baixa o exame, move imediatamente para o `Incoming` do OsiriX e remove o arquivo temporário. O gerenciamento de histórico é feito apenas via metadados (JSON), sem ocupar espaço duplicado.
*   **Pipeline**: Mantém os arquivos localmente (como Persistent), força geração de metadados e permite envio de payload para API externa.

### Integração com Visualizadores
*   **RadiAnt**: Abre a pasta do exame diretamente.
*   **OsiriX / Horos**:
    *   Usa o esquema de URL `osirix://?methodName=displayStudy&AccessionNumber=...` para integração nativa.
    *   **Auto-Move**: Move exames baixados para a pasta `Incoming` do OsiriX (modo Transient).

### Interface & Usabilidade
*   **GUI Moderna (CustomTkinter)**: Interface moderna com cantos arredondados, temas nativos e alta compatibilidade.
*   **Lista Dinâmica**: Exibe exames ordenados alfabeticamente por Nome do Paciente.
*   **Controle de Retenção Dinâmico**: Slider na interface para ajustar o limite de exames (`max_exames`) em tempo real.
*   **Download Manual**: Selecione o servidor (HBR/HAC) e digite apenas o *Accession Number* para baixar.
*   **Busca e Filtragem**: Barra de busca para filtrar exames instantaneamente por Nome, AN, Modalidade ou Descrição.
*   **Prompt Centralizado**: O prompt padrão de tradução do pipeline fica em `/Users/rodrigo/Nox/prompt_translation.py`.

---

## ⚙️ Configuração (`config.ini`)

Todas as preferências são gerenciadas no arquivo `config.ini`.

Credenciais também podem ser fornecidas por ambiente (`USER`/`PASS` ou `USUARIO`/`SENHA`) e por arquivo `.env` na raiz do projeto.
Para compatibilidade, `[AUTH]` no `config.ini` continua tendo prioridade.

```ini
[AUTH]
user = SEU_USUARIO
pass = SUA_SENHA

[OPERATIONAL SYSTEM]
# Sistema operacional: windows, linux ou macos
system = linux

[PATHS]
## Caminho local para modos persistent/pipeline (opcional; tem prioridade)
persistent_dir = data/DICOM
# [MacOS/Linux] Caminho da pasta de entrada do OsiriX
osirix_incoming = /Users/rodrigo/OsiriX Data.nosync/INCOMING.noindex
# [Windows] Caminho da pasta de entrada mapeada (Network Drive) ou local
osirix_incoming_mapped = W:\\

# Caminhos RadiAnt (Windows Only)
radiant_exe = C:\\Program Files\\RadiAntViewer\\RadiAntViewer.exe
radiant_dicom = C:\\DICOM

# Linux/macOS (usado quando system = linux ou macos)
# Caminho relativo ao script ou absoluto - Default: data/DICOM
linux_dicom = data/DICOM

[SETTINGS]
# Intervalo de verificação (segundos)
loop_interval = 150
# Máximo de exames convertidos/mantidos e Limite do Slider
max_exames = 50
slider_max = 200
# Threads de download simultâneo
threads = 15
# Tema da interface (light ou dark) - Default: dark
theme = dark
# Visualizador preferencial: radiant ou osirix
viewer = osirix
# storage_mode: transient, persistent ou pipeline
storage_mode = persistent
# save_metadata: true/false (metadado legado ainda aceito)
save_metadata = false
# Lista de Cenários (nomes dos arquivos em queries/ sem extensão .json)
scenarios = ["plantao-rx", "plantao-tc-rm-us"]

[PIPELINE]
enabled = true
api_url = https://sua-api/exams
api_token = SEU_TOKEN
timeout = 30
strict = false
# request_format: json (default) ou multipart_single_file
request_format = multipart_single_file
# permite pipeline também no modo transient
on_transient = false
# critérios de elegibilidade do exame (CSV, comparado em uppercase)
include_terms = TORAX
exclude_terms = PERFIL
# grava automaticamente o laudo após resposta da API
auto_write_report = true
# fallback para médico executante quando metadata_cockpit não tiver id
default_medico_id =
```

Regras atuais do modo `pipeline`:
- Envia para API apenas quando o exame passa no filtro `include_terms` e não bate em `exclude_terms`.
- A idade é extraída do DICOM (`PatientAge`, ex: `045Y` -> `45 year old`).
- Para envio multipart com múltiplos DICOMs, usa o primeiro arquivo da 2ª série; se houver apenas 1 série, usa o 2º arquivo.
- Em `multipart_single_file`, o formulário enviado é: `file`, `age` e `identificador=<AN>`.
- A resposta da API é gravada integralmente em `/Users/rodrigo/Nox/data/DICOM/<AN>/pipeline_response.json` (ou no `persistent_dir` configurado).
- Após resposta com sucesso, o laudo é montado e gravado automaticamente usando `id_exame_pedido` do `metadata_cockpit.json`.
- O médico executante é lido de `PIPELINE.default_medico_id` (ou env `MEDICO_EXECUTANTE_ID`).
- O envio para Cockpit usa apenas o endpoint padrão `laudar` (revisão desativada por segurança).
- Por segurança, o payload de laudo usa `pendente=true` como padrão.

---

## ▶️ Como Executar

### Instalação

#### Windows 🪟

1.  **Clone o repositório:**
    ```powershell
    git clone https://github.com/rod-americo/Nox.git
    cd Nox
    ```

2.  **Crie e ative o ambiente virtual:**
    ```powershell
    python -m venv venv
    .\venv\Scripts\Activate
    ```

3.  **Instale as dependências:**
    ```powershell
    pip install -r requirements.txt
    playwright install chromium
    ```

#### macOS 🍎 / Linux 🐧

1.  **Clone e Configure:**
    ```bash
    git clone https://github.com/rod-americo/Nox.git
    cd Nox
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    playwright install chromium
    ```

    > **Nota para macOS (Homebrew):** Se encontrar erro de `_tkinter`, instale o suporte gráfico separadamente:
    > ```bash
    > brew install python-tk@3.12
    > ```

---

### Execução de Scripts

#### `nox.py` — Interface Gráfica (GUI)

**Descrição**: Ponto de entrada principal com interface gráfica Tkinter.

**Uso Básico**:
```bash
# Modo GUI (padrão)
python nox.py

# Modo CLI (sem GUI)
python nox.py --cli
```

**Argumentos**:
- `--gui`, `-g`: Executa com interface gráfica (padrão)
- `--cli`, `-c`: Executa em modo linha de comando
- `--no-prepare`: Pula etapa de preparação (Playwright/Login)
- `cenarios`: Lista de cenários para monitorar.
- No modo `--cli`, argumentos extras são repassados para `loop.py` (ex.: `--once`, `--fetch-limit`, `--pipeline-on-transient`).

#### `loop.py` — Modo Headless/Automação

**Descrição**: Orquestrador principal sem interface gráfica. Suporta:
1.  **Nome Simples** (ex: `MONITOR`): resolve para `data/payload_MONITOR.json`.
2.  **Arquivo JSON** (ex: `queries/meu_teste.json`): usa o arquivo informado.

**Uso**:
```bash
# Usa cenários do config.ini
python loop.py

# Usa um cenário específico (busca payload em data/)
python loop.py MONITOR

# Usa um arquivo de query específico
python loop.py queries/plantao.json

# Executa um único ciclo (fetch + download) e encerra
python loop.py MONITOR --once

# Limita a coleta para 100 ANs por ciclo
python loop.py MONITOR --fetch-limit 100

# transient + pipeline via CLI
python loop.py MONITOR --storage-mode transient --pipeline-enabled --pipeline-on-transient
```

#### `fetcher.py` — Busca de Exames via API

**Descrição**: Cliente da API Cockpit. Agora utiliza **Rich** para display de progresso.

**Uso**:
```bash
# Buscar por cenário pré-definido
python fetcher.py MONITOR

# Buscar usando arquivo JSON (argumento posicional)
python fetcher.py queries/plantao.json

# Limitar a coleta e salvar TXT (1 AN por linha)
python fetcher.py MONITOR MONITOR_RX --limit 100 --output-txt

# Modo Raw (Munin) - Salva JSON completo
python fetcher.py --raw MONITOR --inicio 2023-01-01 --fim 2023-01-02
```

#### `downloader.py` — Download Manual

**Descrição**: Motor de download com barra de progresso **Rich**.

**Uso**:
```bash
# Download único
python downloader.py HAC 12345678

# Download único com override de storage mode
python downloader.py HAC 12345678 --storage-mode pipeline

# Batch (lê do clipboard)
python downloader.py
```

#### `scripts/dataset_rx_por_medico.py` — Dataset RX (Fine-tuning)

**Descrição**: Extrai exames RX laudados por médico, baixa DICOM, converte para JPG e gera `finetune.jsonl`.

**Saída**:
- `images/` com JPGs nomeados em formato global (`AN_001.jpg`, `AN_002.jpg`, ...)
- `finetune.jsonl` com pares `messages` (`user` com lista de imagens do estudo, `assistant` com `texto_plano`)
- `checkpoint.json`, `jsonl_index.json`, `fetch_queue.json`, `run.log` (suporte a retomada)
- opcionalmente (modo não-lean): pasta por AN com `dicom/`, `metadata_cockpit.json`, `laudo.json`

**Uso**:
```bash
# Execução padrão com retomada
python scripts/dataset_rx_por_medico.py --query data/payload_rx_exec_52455_100d.json --medico-id 52455 --role executante --storage-mode pipeline --copy-dicom --output-dir data/dataset_ft_plinio_100d

# Retomada sem refazer fetch (usa fetch_queue.json)
python scripts/dataset_rx_por_medico.py --query data/payload_rx_exec_52455_100d.json --medico-id 52455 --role executante --storage-mode pipeline --copy-dicom --output-dir data/dataset_ft_plinio_100d --resume

# Forçar novo fetch da fila
python scripts/dataset_rx_por_medico.py --query data/payload_rx_exec_52455_100d.json --medico-id 52455 --role executante --storage-mode pipeline --copy-dicom --output-dir data/dataset_ft_plinio_100d --refresh-fetch-queue

# Reduzir taxa de chamadas entre exames
python scripts/dataset_rx_por_medico.py --query data/payload_rx_exec_52455_100d.json --medico-id 52455 --role executante --storage-mode pipeline --copy-dicom --output-dir data/dataset_ft_plinio_100d --delay-seconds 1.5

# Modo lean (não salva pasta por AN; mantém apenas images/jsonl/arquivos de controle)
python scripts/dataset_rx_por_medico.py --query data/payload_rx_exec_52455_100d.json --medico-id 52455 --role executante --storage-mode pipeline --output-dir data/dataset_ft_plinio_100d --resume --retry-failed --delay-seconds 1.5 --lean
```

---

## 📋 Requisitos

*   Python 3.9+
*   Dependências (`requirements.txt`):
    *   `playwright`
    *   `requests`
    *   `pydicom`
    *   `rich`
    *   `numpy`
    *   `Pillow`
    *   `pylibjpeg`
    *   `pylibjpeg-libjpeg`
    *   `python-gdcm`

---

## 🛠️ Troubleshooting

### Erro ao converter DICOM para JPG (JPEG Lossless)

Se aparecer erro relacionado a `Unable to decompress 'JPEG Lossless...'`, garanta os decoders instalados:

```bash
pip install pylibjpeg pylibjpeg-libjpeg python-gdcm
```

Validação rápida:
```bash
python -m pip show pylibjpeg pylibjpeg-libjpeg python-gdcm
```

### Retomada de execução em datasets grandes

O `scripts/dataset_rx_por_medico.py` já suporta retomada:
- `checkpoint.json`: status por AN (`done/failed/pending`)
- `jsonl_index.json`: evita duplicar linhas no `finetune.jsonl`
- `fetch_queue.json`: reutiliza fila de ANs sem refazer fetch
- `run.log`: trilha de execução

Comportamento padrão:
- reiniciando no mesmo `--output-dir`, o script pula concluídos e continua dos pendentes.
- quando a query não muda, ele reaproveita `fetch_queue.json` e evita nova chamada de fetch.

### Modo lean para reduzir I/O

Use `--lean` quando o objetivo for somente treinamento. Nesse modo ele não persiste:
- `dicom/`
- `metadata_cockpit.json`
- `laudo.json`

Mantém apenas `images/`, `finetune.jsonl` e arquivos de controle (`checkpoint/jsonl_index/fetch_queue/run.log`).

Para forçar nova consulta de fila:
```bash
python scripts/dataset_rx_por_medico.py ... --refresh-fetch-queue
```

Desenvolvido para agilizar o fluxo de trabalho radiológico. v2.1.0
