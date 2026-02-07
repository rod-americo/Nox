#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prompt centralizado para tradução radiológica.

Este arquivo existe para facilitar manutenção sem precisar editar
lógica de downloader/config.
"""

THORAX_XRAY_TRANSLATION_PROMPT = """Converta o laudo para português radiológico brasileiro em estilo telegráfico (nominal), mantendo estrita fidelidade ao conteúdo e ao grau de certeza.

Formato obrigatório:
- Entregar somente estes descritores, nesta ordem:
	*Pulmões:*
	*Pleura:*
	*Coração:*
	*Parede torácica:*
	*Dispositivos:*
- Sem cabeçalhos “FINDINGS/IMPRESSION”.
- Sem introduções.
- Após “:”, iniciar com letra minúscula.
- Frases curtas, sem verbos de ligação (“é”, “está”, “encontra-se”).
- Preferir “sem ...”, “com ...”, “aumentada”, “preservada”, “obliterados”.

Regras clínicas/terminológicas:
1) Não criar seção “Vascularização pulmonar”; incorporar conteúdo vascular em *Pulmões:* ou *Coração:*.
2) “unremarkable” -> “sem alterações”.
3) Evitar artigos desnecessários no início (“mediastino sem alterações”, não “o mediastino...”).
4) Em *Dispositivos:*, nunca traduzir “lines” como “linhas”; usar “cateter(es)”/“dispositivo(s)”.
5) Padronizações:
	- right-sided PICC line -> cateter de acesso venoso central de inserção periférica pelo membro superior direito
	- left-sided PICC line -> cateter de acesso venoso central de inserção periférica pelo membro superior esquerdo
	- left-sided central line -> cateter de acesso venoso central à esquerda
	- right internal jugular central line -> cateter de acesso venoso central transjugular à direita
	- right subclavian central line -> cateter de acesso venoso central trans-subclávio à direita
	- left subclavian central line -> cateter de acesso venoso central trans-subclávio à esquerda
	- NG tube / enteric tube -> sonda transesofágica
	- tracheostomy tube -> cânula de traqueostomia
	- endotracheal tube -> tubo endotraqueal
	- pacemaker -> dispositivo de eletroestimulação cardíaca
6) Sempre que a extremidade distal do dispositivo estiver descrita no original, preservar a topografia.
7) Se a ponta/extremidade não estiver descrita no original, não inventar posição.
8) Se houver conteúdo bilateral equivalente para ângulos costofrênicos, consolidar como “seios costofrênicos obliterados”.
9) Manter incerteza do original (possible/probable -> possível/provável), sem aumentar certeza.
10) Não usar “inespecífico” quando o sentido for normalidade sem alterações."""

