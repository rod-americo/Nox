import json
import sys
import argparse
from transfer_scenarios import CockpitSession
import config

def verify_structure():
    session = CockpitSession(headless=True)
    print("Iniciando sessão para inspeção...")
    session.start()
    
    try:
        if not session.login(config.USUARIO, config.SENHA):
            print("Login falhou.")
            return

        print("Listando cenários...")
        cenarios = session.get_cenarios()
        
        if not cenarios:
            print("Nenhum cenário retornado.")
            return
            
        print(f"Encontrados {len(cenarios)} cenários.")
        
        # Salva o dump completo para análise
        with open("cenarios_dump.json", "w", encoding="utf-8") as f:
            json.dump(cenarios, f, indent=2, ensure_ascii=False)
            
        print("Dump salvo em 'cenarios_dump.json'.")
        
        # Análise rápida do primeiro cenário que tiver filtros
        for c in cenarios:
            filtros = c.get("filtros")
            colunas = c.get("colunas")
            
            # Se encontrar algum com filtros preenchidos, mostra
            if filtros or colunas:
                print(f"\n--- Exemplo: {c.get('nm_cenario')} ---")
                print(f"Tem filtros? {bool(filtros)}")
                print(f"Tem colunas? {bool(colunas)}")
                if filtros:
                    print(f"Filtros Keys: {filtros.keys() if isinstance(filtros, dict) else 'Not Dict'}")
                break
        else:
            print("\nAVISO: Nenhum cenário da lista parece ter 'filtros' ou 'colunas' preenchidos/não-vazios.")

    finally:
        session.stop()

if __name__ == "__main__":
    verify_structure()
