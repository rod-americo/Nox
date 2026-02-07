#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt centralizado para tradução radiológica.

Este arquivo existe para facilitar manutenção sem precisar editar
lógica de downloader/config.
"""

THORAX_XRAY_TRANSLATION_PROMPT = """Tarefa: traduzir o laudo para português radiológico brasileiro em estilo telegráfico (nominal), com fidelidade estrita ao conteúdo original e ao grau de certeza.

Saída obrigatória:
- Retornar somente 5 linhas, nesta ordem exata:
  *Pulmões:* <conteúdo>
  *Pleura:* <conteúdo>
  *Coração e mediastino:* <conteúdo>
  *Parede torácica:* <conteúdo>
  *Dispositivos:* <conteúdo>
- Não incluir cabeçalhos (ex.: FINDINGS/IMPRESSION), introduções ou conclusão.
- Após os dois-pontos, iniciar em minúscula.
- Usar frases curtas, sem verbos de ligação ("é", "está", "encontra-se").
- Se uma seção não tiver achado no original: usar "sem alterações relevantes".

Regras clínicas e terminológicas:
1) Não criar seção "Vascularização pulmonar"; distribuir conteúdo vascular em *Pulmões:* ou *Coração:*.
2) "unremarkable" -> "sem alterações".
3) Evitar artigo no início quando dispensável (ex.: "mediastino sem alterações").
4) Em *Dispositivos:*, nunca traduzir "line(s)" como "linha(s)"; usar "cateter(es)", "sonda(s)", "dispositivo(s)".
5) Padronizações obrigatórias:
   - right-sided PICC line -> cateter de acesso venoso central de inserção periférica pelo membro superior direito
   - right-sided central line -> cateter de acesso venoso central à direita
   - right internal jugular central line -> cateter de acesso venoso central transjugular à direita
   - right subclavian central line -> cateter de acesso venoso central trans-subclávio à direita
   - NG tube / enteric tube -> sonda transesofágica
   - tracheostomy tube -> cânula pela traqueostomia
   - endotracheal tube -> tubo endotraqueal
   - pacemaker -> dispositivo de eletroestimulação cardíaca
6) Quando a extremidade distal do dispositivo estiver no original, preservar a topografia.
7) Se a ponta/extremidade não estiver descrita, não inferir posição.
8) Achados bilaterais equivalentes nos ângulos costofrênicos -> "seios costofrênicos obliterados".
9) Preservar incerteza do original (possible/probable/suspected -> possível/provável/suspeito), sem aumentar certeza.
10) Não usar "inespecífico" para expressar normalidade.

Controle de qualidade antes de responder:
- Confirmar que existem exatamente 5 linhas e 5 descritores.
- Confirmar ausência de informação não contida no original.
- Confirmar consistência terminológica nas 5 seções."""
