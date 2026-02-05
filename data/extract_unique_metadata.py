#!/usr/bin/env python3
"""
Script para extrair valores únicos de campos de metadados das subpastas em DICOM.

Campos extraídos:
- _cockpit.exame (de metadata_cockpit.json)
- _dicom.Body Part Examined (de metadado_*_dicom.json)
- _dicom.Study Description (de metadado_*_dicom.json)
- _dicom.Series Description (de metadado_*_dicom.json)
"""

import os
import json
from pathlib import Path
from collections import defaultdict

def extract_unique_metadata():
    """Extrai valores únicos de campos de metadados."""
    
    script_dir = Path(__file__).parent
    dicom_dir = script_dir / "DICOM"
    
    if not dicom_dir.exists():
        print(f"Erro: Diretório DICOM não encontrado em {dicom_dir}")
        return
    
    # Dicionário para armazenar valores únicos
    unique_values = {
        "_cockpit.exame": set(),
        "_dicom.Body Part Examined": set(),
        "_dicom.Study Description": set(),
        "_dicom.Series Description": set(),
    }
    
    # Contadores
    total_folders = 0
    processed_folders = 0
    
    # Iterar sobre cada subpasta em DICOM
    for subfolder in sorted(dicom_dir.iterdir()):
        if not subfolder.is_dir():
            continue
            
        total_folders += 1
        
        # Extrair dados do metadata_cockpit.json
        cockpit_file = subfolder / "metadata_cockpit.json"
        if cockpit_file.exists():
            try:
                with open(cockpit_file, 'r', encoding='utf-8') as f:
                    cockpit_data = json.load(f)
                    if "exame" in cockpit_data and cockpit_data["exame"]:
                        unique_values["_cockpit.exame"].add(cockpit_data["exame"])
            except (json.JSONDecodeError, IOError) as e:
                print(f"Aviso: Erro ao ler {cockpit_file}: {e}")
        
        # Extrair dados dos arquivos metadado_*_dicom.json
        dicom_files = list(subfolder.glob("metadado_*_dicom.json"))
        for dicom_file in dicom_files:
            try:
                with open(dicom_file, 'r', encoding='utf-8') as f:
                    dicom_data = json.load(f)
                    
                    if "Body Part Examined" in dicom_data and dicom_data["Body Part Examined"]:
                        unique_values["_dicom.Body Part Examined"].add(dicom_data["Body Part Examined"])
                    
                    if "Study Description" in dicom_data and dicom_data["Study Description"]:
                        unique_values["_dicom.Study Description"].add(dicom_data["Study Description"])
                    
                    if "Series Description" in dicom_data and dicom_data["Series Description"]:
                        unique_values["_dicom.Series Description"].add(dicom_data["Series Description"])
                        
            except (json.JSONDecodeError, IOError) as e:
                print(f"Aviso: Erro ao ler {dicom_file}: {e}")
        
        processed_folders += 1
    
    # Exibir resultados
    print("=" * 80)
    print("VALORES ÚNICOS EXTRAÍDOS DOS METADADOS DICOM")
    print("=" * 80)
    print(f"\nTotal de pastas processadas: {processed_folders}/{total_folders}")
    print()
    
    for field, values in unique_values.items():
        print("-" * 80)
        print(f"\n{field} ({len(values)} valores únicos):\n")
        for value in sorted(values):
            print(f"  • {value}")
        print()
    
    # Salvar em arquivo JSON para referência
    output_file = script_dir / "unique_metadata_values.json"
    output_data = {
        "total_folders_processed": processed_folders,
        "fields": {}
    }
    for field, values in unique_values.items():
        output_data["fields"][field] = sorted(list(values))
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print("=" * 80)
    print(f"Resultados salvos em: {output_file}")
    print("=" * 80)

if __name__ == "__main__":
    extract_unique_metadata()
