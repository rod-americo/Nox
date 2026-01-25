# Nox — WADO/DICOM Assistant

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
*   **GUI Moderna (Flet)**: Interface responsiva, Always-on-Top, com tema ajustável.
*   **Lista Dinâmica**: Exibe exames ordenados alfabeticamente por Nome do Paciente.
*   **Controle de Retenção Dinâmico**: Slider na interface para ajustar o limite de exames (`max_exames`) em tempo real (5 a 200).
*   **Download Manual**: Selecione o servidor (HBR/HAC) e digite apenas o *Accession Number* para baixar.
*   **Busca e Filtragem**: Barra de busca para filtrar exames instantaneamente por Nome, AN, Modalidade ou Descrição.

---

## ⚙️ Configuração (`config.ini`)

Todas as preferências são gerenciadas no arquivo `config.ini`.

```ini
[AUTH]
user = SEU_USUARIO
pass = SUA_SENHA

[PATHS]
# [MacOS/Linux] Caminho da pasta de entrada do OsiriX
osirix_incoming = /Users/rodrigo/OsiriX Data.nosync/INCOMING.noindex
# [Windows] Caminho da pasta de entrada mapeada (Network Drive) ou local
osirix_incoming_mapped = W:\

# Caminhos RadiAnt (Windows Only)
radiant_exe = C:\Program Files\RadiAntViewer\RadiAntViewer.exe
radiant_dicom = C:\DICOM

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
# Lista de Cenários (separados por vírgula ou JSON)
scenarios = ["planto-rx", "plantao-tc= rm-us"]
```

---

## ▶️ Como Executar

O projeto conta com um script unificado `nox.py`.

### Instalação

#### Pré-requisitos: Git

Você precisará do **Git** para baixar o projeto.

*   **Windows 🪟**
    *   **Instalador**: [git-scm.com](https://git-scm.com/download/win)
    *   **Terminal**: `winget install --id Git.Git -e --source winget`

*   **macOS 🍎**
    *   **Opção 1 (Recomendada - Xcode Command Line Tools)**:
        Abra o terminal e digite:
        ```bash
        xcode-select --install
        ```
    *   **Opção 2 (Homebrew)**:
        ```bash
        brew install git
        ```

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

#### macOS 🍎

1.  **Clone o repositório:**
    ```bash
    git clone https://github.com/rod-americo/Nox.git
    cd Nox
    ```

2.  **Crie e ative o ambiente virtual:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Instale as dependências:**
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

### Execução

Sempre ative o ambiente virtual a partir do diretório do projeto antes de rodar (`.\venv\Scripts\Activate` ou `source venv/bin/activate`).

#### Modo GUI (Interface Gráfica)
```bash
python nox.py
```
*Funcionalidades da GUI:*
*   **Seleção de Cenários em Tempo Real**: Novo painel "Cenários" permite carregar a lista completa do servidor e marcar/desmarcar quais devem ser monitorados. A escolha é salva automaticamente.
*   **Slider Dinâmico**: O limite do slider ajusta-se ao valor de `slider_max` no INI.
*   **Download Manual**: Interface intuitiva com botões de rádio para escolha do servidor.

#### Modo CLI (Terminal)
Ideal para debug ou execução leve. Use **aspas** para nomes compostos.
```bash
python nox.py --cli
```
*Opções:*
*   `python nox.py --cli MONITOR`: Roda CLI apenas para o cenário MONITOR.
*   `python nox.py --cli "CENARIO COMPOSTO" MONITOR`: Exemplo com nomes contendo espaços.
*   `python nox.py --cli --no-prepare`: Roda CLI pulando a preparação.

---

## 🛠 Estrutura Técnica

*   **`nox.py`**: Ponto de entrada e Interface Gráfica (Flet). Gerencia estado (`AppState`), contador de sessão e lista de exames.
*   **`loop.py`**: Orquestrador (CLI/Backend). Monitora ciclo de vida, verifica retenção (`verificar_retencao_exames`) e dispara downloads.
*   **`downloader.py`**: Motor de download. Lida com retry, extração de metadados DICOM e lógica `Storage Mode` (move vs save).
*   **`config.py`**: Carregador de configurações singleton.
*   **`prepare.py`**: Automação *headless* (Playwright) para login e captura de tokens.

### Utilitários

#### Mapear Cenários (`prepare.py`)
Lista todos os cenários disponíveis na conta configurada.
```bash
python prepare.py --mapear-cenarios
```

#### Download em Batch (`downloader.py`)
Baixa exames em massa usando uma lista de _Accession Numbers_ (ANs) copiados para a área de transferência.

1.  Copie os ANs (um por linha) para o Clipboard.
2.  Execute:
    ```bash
    # Tenta HAC -> HBR automaticamente
    python downloader.py
    
    # Força servidor específico
    python downloader.py HAC
    ```

---

## 📘 Scripts Standalone

O Nox é composto por vários scripts modulares que podem ser executados de forma independente. Abaixo está a documentação completa de cada um.

### 🎯 Pontos de Entrada Principais

#### `nox.py` — Interface Gráfica (GUI)

**Descrição**: Ponto de entrada principal com interface gráfica moderna (Flet). Ideal para uso interativo.

**Uso Básico**:
```bash
# Modo GUI (padrão)
python nox.py

# Modo GUI com cenários específicos
python nox.py MONITOR MONITOR_RX

# Modo CLI (sem interface gráfica)
python nox.py --cli

# Modo CLI com cenários específicos
python nox.py --cli MONITOR

# Pular etapa de preparação (login)
python nox.py --no-prepare
```

**Argumentos**:
- `--gui`, `-g`: Executa com interface gráfica (padrão)
- `--cli`, `-c`: Executa em modo linha de comando (sem GUI)
- `--no-prepare`: Pula etapa de preparação (Playwright/Login)
- `cenarios`: Lista de cenários para monitorar (ex: `MONITOR MONITOR_RX`)

**Quando usar**:
- ✅ Quando você quer interface visual e controle manual
- ✅ Para monitorar downloads em tempo real
- ✅ Para fazer downloads manuais pontuais

---

#### `loop.py` — Modo Headless/Automação

**Descrição**: Orquestrador principal sem interface gráfica. Ideal para execução em background, servidores ou automação.

**Uso Básico**:
```bash
# Usa cenários do config.ini
python loop.py

# Com arquivos de consulta específicos
python loop.py queries/plantao-rx.json queries/monitor.json

# Pular login (usar sessão existente)
python loop.py --no-prepare
```

**Argumentos**:
- `cenarios`: Caminhos para arquivos JSON de payload (em `queries/`)
- `--no-prepare`: Pula etapa de preparação (login)

**Quando usar**:
- ✅ Para execução em background/servidor
- ✅ Para automação via cron/systemd
- ✅ Quando não precisa de interface gráfica

**Diferença entre `nox.py --cli` e `loop.py`**:
- `nox.py --cli`: Wrapper que chama `loop.py` internamente
- `loop.py`: Execução direta do orquestrador

---

### 🔧 Utilitários

#### `downloader.py` — Download Manual

**Descrição**: Motor de download WADO/DICOM. Permite download manual de exames individuais ou em lote.

**Uso Básico**:
```bash
# Download único
python downloader.py HAC 12345678

# Batch com servidor específico (lê ANs do clipboard)
python downloader.py HAC

# Batch com auto-detect (tenta HAC → HBR)
python downloader.py

# Desativar barra de progresso
python downloader.py HAC 12345678 --no-progress
```

**Argumentos**:
- `servidor`: Nome do servidor (`HBR` ou `HAC`) - opcional em modo batch
- `an`: Accession Number - opcional, se omitido lê do clipboard
- `--no-progress`, `-np`: Desativa barra de progresso

**Modos de Operação**:
1. **Download Único**: `python downloader.py SERVER AN`
2. **Batch Servidor Específico**: `python downloader.py SERVER` (lê ANs do clipboard)
3. **Batch Auto-Detect**: `python downloader.py` (tenta HAC, fallback para HBR)

**Quando usar**:
- ✅ Para baixar exames específicos manualmente
- ✅ Para processar lista de ANs em lote
- ✅ Para testar download de um exame específico

---

#### `fetcher.py` — Busca de Exames via API

**Descrição**: Cliente da API Cockpit. Busca exames disponíveis baseado em cenários/filtros.

**Uso Básico**:
```bash
# Buscar por cenário pré-definido
python fetcher.py MONITOR

# Buscar múltiplos cenários
python fetcher.py MONITOR MONITOR_RX DIA_U

# Buscar usando arquivo de payload JSON
python fetcher.py --file queries/plantao-rx.json

# Buscar múltiplos arquivos
python fetcher.py --file queries/monitor.json queries/plantao-rx.json

# Modo raw (salva JSON completo)
python fetcher.py --raw MONITOR

# Listar cenários disponíveis
python fetcher.py --list
```

**Argumentos**:
- `cenarios`: Nomes de cenários pré-definidos (ex: `MONITOR`, `DIA_U`)
- `--file`, `-f`: Caminho para arquivo(s) JSON de payload
- `--raw`: Modo raw/Munin (salva JSON completo em `data/`)
- `--list`, `-l`: Lista cenários disponíveis no servidor

**Cenários Pré-Definidos**:
- `MONITOR`: CT/MR/US - Urgente/Internado - Não Assinado
- `MONITOR_RX`: RX - Urgente/Internado - Não Assinado
- `DIA_E`: Eletivo (24 horas)
- `DIA_U`: Urgente (3 horas)
- `DIAS_I`: Internado (36 horas)
- `MENSAL`, `SEMANAL`: Períodos mais longos

**Quando usar**:
- ✅ Para testar consultas à API
- ✅ Para criar novos arquivos de payload
- ✅ Para debug de filtros e cenários

---

#### `query.py` — Consulta de Metadados WADO

**Descrição**: Cliente WADO-Query. Obtém metadados de um exame (StudyUID, SeriesUIDs, SOPInstanceUIDs).

**Uso Básico**:
```bash
# Consulta básica
python query.py HAC 12345678

# Saída JSON limpa (sem logs)
python query.py HAC 12345678 --json

# Consultar HBR
python query.py HBR 12345678
```

**Argumentos**:
- `servidor`: Nome do servidor (`HBR` ou `HAC`)
- `an`: Accession Number
- `--json`: Saída JSON limpa, sem logs (útil para scripts)

**Saída**:
```json
{
  "an": "12345678",
  "study_uid": "1.2.840...",
  "total_instances": 150,
  "series": [
    {
      "series_uid": "1.2.840...",
      "instances": ["1.2.840...", ...]
    }
  ]
}
```

**Quando usar**:
- ✅ Para verificar se um exame existe no servidor
- ✅ Para obter metadados sem baixar as imagens
- ✅ Para debug de problemas de download

---

#### `prepare.py` — Preparação e Login

**Descrição**: Automação Playwright para login no Cockpit e captura de sessão/tokens.

**Uso Básico**:
```bash
# Login e preparação padrão
python prepare.py

# Mapear todos os cenários disponíveis
python prepare.py --mapear-cenarios

# Listar cenários (alias)
python prepare.py --list
```

**Argumentos**:
- `--mapear-cenarios`: Lista todos os cenários disponíveis na conta
- `--list`, `-l`: Alias para `--mapear-cenarios`

**Quando usar**:
- ✅ Para renovar sessão expirada
- ✅ Para descobrir novos cenários disponíveis
- ✅ Para debug de problemas de autenticação

**Nota**: Este script é executado automaticamente pelo `loop.py` e `nox.py`, a menos que `--no-prepare` seja usado.

---

## 🔄 Fluxo de Uso Recomendado

### Para quem quer GUI:
```bash
python nox.py
```
- Interface visual completa
- Controle manual de downloads
- Monitoramento em tempo real

### Para quem não quer GUI (automação):
```bash
python loop.py
```
- Execução em background
- Ideal para servidores
- Sem dependência de interface gráfica

### Para downloads manuais pontuais:
```bash
# Copie os ANs para o clipboard, depois:
python downloader.py
```

---

## 📋 Requisitos

*   Python 3.9+
*   Dependências (instale via `pip install -r requirements.txt` com o ambiente virtual ativado):
    *   `flet`
    *   `requests`
    *   `pydicom`
    *   `tqdm`
    *   `playwright`

Desenvolvido para agilizar o fluxo de trabalho radiológico.