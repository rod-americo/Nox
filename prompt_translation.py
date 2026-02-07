#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt centralizado para tradução radiológica.

Este arquivo existe para facilitar manutenção sem precisar editar
lógica de downloader/config.
"""

THORAX_XRAY_TRANSLATION_PROMPT = """Traduza para português radiológico brasileiro, sem abreviações desnecessárias (ex.: preferir “anteroposterior” em vez de “AP”), mantendo fidelidade ao conteúdo original e ao grau de certeza.

Regras obrigatórias:
1) Entregar somente os descritores em tópicos, sem cabeçalhos “FINDINGS” ou “IMPRESSION”.
2) Iniciar no primeiro descritor do corpo (não incluir introduções como “Padrão radiológico”).
3) Usar marcador em negrito no descritor, por exemplo: *Pulmões e pleuras:*.
4) Após “:”, iniciar com letra minúscula.
5) Se houver duas orações no mesmo descritor, separar por “;”.
6) Não criar o descritor “Vascularização pulmonar:”.
	- Quando houver conteúdo vascular, incorporar em *Pulmões e pleuras:* ou *Coração e mediastino:*.
7) O descritor final deve ser *Dispositivos:* (nunca “Linhas, tubos e dispositivos”).
8) Nunca traduzir “lines” como “linhas” nesse contexto; usar “cateter(es)”/“dispositivo(s)”.
9) Normalizações terminológicas:
	- “right-sided PICC line” -> “cateter de acesso venoso central de inserção periférica pelo membro superior direito”
	- “left-sided central line” -> “cateter de acesso venoso central à esquerda”
	- “right internal jugular central line” -> “cateter de acesso venoso central transjugular à direita”
10) Sempre que possível, explicitar a localização da extremidade dos cateteres (ex.: “extremidade na veia cava superior”).
11) Se houver menção bilateral equivalente a “ângulo costofrênico direito obliterado” e “ângulo costofrênico esquerdo obliterado”, consolidar para “seios costofrênicos obliterados”.
12) Não aumentar certeza diagnóstica: termos como “possible” devem permanecer como incerteza equivalente (ex.: “possíveis atelectasias”)."""
