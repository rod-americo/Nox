# Documentação de Payloads de Consulta (Queries)

Este diretório contém arquivos JSON que definem os critérios de pesquisa (payloads) enviados para a API do Cockpit via `fetcher.py`. Eles são usados para monitorar e baixar exames automaticamente com base em filtros específicos.

## Estrutura do Payload

Cada arquivo JSON representa um objeto de busca. Os campos mais importantes são descritos abaixo:

### Filtros Principais

| Campo | Descrição | Exemplos/Valores |
| :--- | :--- | :--- |
| `id_origem_atendimento` | Lista de IDs de origem do atendimento. | `["1", "2"]` (Eletivo), `["3"]` (Urgente), `["4"]` (Internado) |
| `cd_modalidade` | Lista de siglas de modalidades DICOM. | `["CT", "MR", "US"]`. *Nota: Para RX, geralmente filtra-se por Id de Procedimento.* |
| `tp_status` | Fila/Status do exame no sistema. | `["NOVO", "ASSINADO", "LAUDADO", "ENTREGUE"]` |
| `assinado` | Filtro para exames assinados ou não. | `["S"]` (Sim), `["N"]` (Não) |
| `imagem` | Filtro para exames que possuem imagem. | `["S"]` (Sim), `["N"]` (Não) |

### Filtros de Procedimento e Exame

Para exames de Raio-X (RX), a prática no projeto é deixar `cd_modalidade` vazio e filtrar por IDs específicos:
- `id_procedimento`: Exemplos comuns incluem `["96"]`.
- `exame.id_exame`: Lista de IDs numéricos (ex: `5742, 4887`).

> [!NOTE]
> Siglas padrão DICOM para RX são geralmente `DX` ou `CR`. O código `RX` não foi encontrado em uso como filtro de modalidade neste projeto.

### Lógica de Datas Dinâmicas

O módulo `fetcher.py` ajusta automaticamente os campos de data (`dt_imagem`, `dt_pedido`, `dt_cadastro`) antes de realizar a consulta.

- **Comportamento**: Ele calcula a diferença (delta) entre `dt_inicio` e `dt_fim` definidos no arquivo JSON.
- **Ajuste**: Ele define `dt_fim` como o momento atual e recalcula `dt_inicio` subtraindo o delta original.
- **Objetivo**: Permitir que uma consulta definida como "exames do último mês" ou "últimas 24h" seja reutilizada perpetuamente no loop de monitoramento.

## Lista de Status Comuns (`tp_status`)

- `NOVO`: Exame recém-chegado.
- `PENDENTELAUDADO`: Aguardando laudo.
- `DITADO` / `DIGITADO`: Processo de laudo em andamento.
- `LAUDADO`: Laudo finalizado, aguardando revisão ou assinatura.
- `REVISADO`: Revisado por um segundo médico.
- `ASSINADO`: Laudo assinado digitalmente.
- `ENTREGUE`: Resultado disponibilizado ao paciente/médico.

## Exemplos de Arquivos

- [plantao-tc-rm-us.json]: Foca em exames de Pronto Socorro e Internados para modalidades de corte e ultra.
- [mi-eletivos.json]: Filtra exames eletivos (ambulatoriais) de Medicina Interna.
- [nr-eletivos.json]: Filtra exames eletivos de Neurorradiologia.

---
> [!TIP]
> Ao criar uma nova consulta, você pode usar uma ferramenta de "Inspect" no navegador enquanto usa o Cockpit para capturar o payload JSON exato enviado em uma busca manual e salvá-lo aqui.
