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

---

## ⚙️ Configuração (`config.ini`)

Todas as preferências são gerenciadas no arquivo `config.ini`.

```ini
[AUTH]
user = SEU_USUARIO
pass = SUA_SENHA

[OPERATIONAL SYSTEM]
# Sistema operacional: windows, linux ou macos
system = linux

[PATHS]
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
# Lista de Cenários (nomes dos arquivos em queries/ sem extensão .json)
scenarios = ["plantao-rx", "plantao-tc-rm-us"]
```

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

#### `loop.py` — Modo Headless/Automação

**Descrição**: Orquestrador principal sem interface gráfica. Suporta argumentos com lógica híbrida:
1.  **Nome Simples** (ex: `MONITOR`): Busca payload em `data/payload_MONITOR.json`.
2.  **Arquivo JSON** (ex: `queries/meu_teste.json`): Usa o arquivo especificado.

**Uso**:
```bash
# Usa cenários do config.ini
python loop.py

# Usa um cenário específico (busca payload em data/)
python loop.py MONITOR

# Usa um arquivo de query específico
python loop.py queries/plantao.json
```

#### `fetcher.py` — Busca de Exames via API

**Descrição**: Cliente da API Cockpit. Agora utiliza **Rich** para display de progresso.

**Uso**:
```bash
# Buscar por cenário pré-definido
python fetcher.py MONITOR

# Buscar usando arquivo JSON
python fetcher.py --file queries/plantao.json

# Modo Raw (Munin) - Salva JSON completo
python fetcher.py --raw MONITOR --inicio 2023-01-01 --fim 2023-01-02
```

#### `downloader.py` — Download Manual

**Descrição**: Motor de download com barra de progresso **Rich**.

**Uso**:
```bash
# Download único
python downloader.py HAC 12345678

# Batch (lê do clipboard)
python downloader.py
```

---

## 📋 Requisitos

*   Python 3.9+
*   Dependências (`requirements.txt`):
    *   `playwright`
    *   `requests`
    *   `pydicom`
    *   `rich`

Desenvolvido para agilizar o fluxo de trabalho radiológico. v2.1.0