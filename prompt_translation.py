#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt centralizado para tradução radiológica.

Este arquivo existe para facilitar manutenção sem precisar editar
lógica de downloader/config.
"""

THORAX_XRAY_TRANSLATION_PROMPT = """Traduza o texto a seguir para português radiológico brasileiro, sem abreviação, por exemplo, 'anteroposterior ao invés de AP', mantendo a estrutura em tópicos (não usar CAIXA ALTA) e o escopo descritivo original. Usar marcadores de negrito (*) entre palavras ou conjuntos de palavras antes de dois-pontos, e eles inclusive, por exemplo, '*Pulmões e pleuras:*'. Preserve todos os sistemas avaliados, mesmo quando normais. Envie apenas o conteúdo de findings/achados (sem incluir tal cabeçalho), ou seja, iniciando com o primeiro sistema avaliado. Utilize terminologia radiológica padronizada (ex.: 'consolidação', 'atelectasia', 'derrame pleural', 'silhueta cardíaca', 'oligoemia'). Converta termos anatômicos para o uso consagrado no Brasil (ex.: 'pulmonary vasculature' -> 'vascularização pulmonar'). Use linguagem objetiva, sem advérbios interpretativos e SEM verbos de ligação, por exemplo, 'seio costofrênico direito obliterado'. Após dois-pontos, iniciar sempre com letra minúscula. Se houver necessidade de duas frases após um descritor seguido de dois pontos, separar por ponto-e-vírgula, iniciando com letra minúscula, por exemplo, 'silhueta cardíaca aumentada; botão aórtico calcificado.'."""
