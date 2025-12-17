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
*   **Download Manual**: Campo para baixar exames específicos digitando `SERVER AN`.

---

## ⚙️ Configuração (`config.ini`)

Todas as preferências são gerenciadas no arquivo `config.ini`.

```ini
[AUTH]
user = SEU_USUARIO
pass = SUA_SENHA

[PATHS]
# Onde os exames serão armazenados (Persistent) ou Temporários (Transient)
radiant_dicom = C:\DICOM
# (Opcional) Apenas para OsiriX/Horos: Pasta monitorada pelo viewer
osirix_incoming = /Users/joedoe/Documents/OsiriX Data/Incoming

# Caminho do executável (Apenas para RadiAnt)
radiant_exe = C:\Program Files\RadiAntViewer\RadiAntViewer.exe

[SETTINGS]
# Intervalo de verificação (segundos)
loop_interval = 150
# Máximo de exames convertidos/mantidos e Limite do Slider
max_exames = 50
slider_max = 200
# Threads de download simultâneo
threads = 15
# Tema da interface: dark ou light
theme = dark
# Título da Janela
title = Nox Assistant
# Visualizador preferencial: radiant ou osirix
viewer = radiant
# Lista de Cenários (separados por vírgula ou JSON)
scenarios = ["MONITOR", "MONITOR_RX"]
```

---

## ▶️ Como Executar

O projeto conta com um script unificado `nox.py`.

### Instalação

1.  Crie e ative um ambiente virtual (recomendado):
    ```bash
    # Windows
    python -m venv .nox
    .\.nox\Scripts\Activate
    
    # Mac/Linux
    python3 -m venv .nox
    source .nox/bin/activate
    ```

2.  Instale as dependências:
    ```bash
    pip install -r requirements.txt
    playwright install chromium
    ```

### Execução

#### Modo GUI (Interface Gráfica) - Padrão
```bash
python nox.py
```
*Funcionalidades da GUI:*
*   **Seleção de Cenários em Tempo Real**: Novo painel "Cenários" permite carregar a lista completa do servidor e marcar/desmarcar quais devem ser monitorados. A escolha é salva automaticamente.
*   **Slider Dinâmico**: O limite do slider ajusta-se ao valor de `slider_max` no INI.

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

### Utilitários de Cenário

O Nox inclui ferramentas para gerenciar cenários do Cockpit:

**1. Mapear Cenários (`prepare.py`)**
Lista todos os cenários disponíveis na conta configurada.
```bash
python prepare.py --mapear-cenarios
```

**2. Transferir/Clonar Cenários (`transfer_scenarios.py`)**
Permite copiar cenários entre contas ou duplicá-los.
```bash
# Clonar na mesma conta:
python transfer_scenarios.py --cenario "ORIGINAL" --novo-nome "COPIA"

# Transferir para outro usuário:
python transfer_scenarios.py --cenario "ORIGINAL" --target-user "OUTRO_USER" --target-pass "SENHA"
```

---

## 📋 Requisitos

*   Python 3.10+
*   Dependências (`install.ps1` instala automaticamente):
    *   `flet`
    *   `requests`
    *   `pydicom`
    *   `tqdm`
    *   `playwright`

Desenvolvido para agilizar o fluxo de trabalho radiológico.