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
scenarios = ["MONITOR", "MONITOR_RX"]
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

## 📋 Requisitos

*   Python 3.9+
*   Dependências (instale via `pip install -r requirements.txt` com o ambiente virtual ativado):
    *   `flet`
    *   `requests`
    *   `pydicom`
    *   `tqdm`
    *   `playwright`

Desenvolvido para agilizar o fluxo de trabalho radiológico.