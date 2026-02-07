#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt centralizado para tradução radiológica.

Este arquivo existe para facilitar manutenção sem precisar editar
lógica de downloader/config.
"""

THORAX_XRAY_TRANSLATION_PROMPT = (
    "Traduza o texto a seguir para português radiológico brasileiro, mantendo a "
    "estrutura em tópicos e o escopo descritivo original, sem criar seção de "
    "achados, impressão ou resumo. Preserve todos os sistemas avaliados, mesmo "
    "quando normais. Utilize terminologia radiológica padronizada (ex.: "
    "'consolidação', 'atelectasia', 'derrame pleural', 'silhueta cardíaca', "
    "'oligoemia'). Converta termos anatômicos para o uso consagrado no Brasil "
    "(ex.: 'pulmonary vasculature' -> 'vascularização pulmonar'). Use linguagem "
    "objetiva, sem advérbios interpretativos e sem verbos. Após dois-pontos, "
    "iniciar sempre com letra minúscula. Se houver necessidade de duas frases "
    "no mesmo descritor, separar por ponto-e-vírgula, iniciando com letra minúscula."
)

