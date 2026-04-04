"""
Convert Word document to PDF and export high-resolution Mermaid diagrams.
Uses LibreOffice for reliable PDF conversion.
"""

import subprocess
import os
import json
from pathlib import Path

def convert_docx_to_pdf(docx_file, output_pdf=None):
    """Convert .docx to .pdf using LibreOffice"""
    if output_pdf is None:
        output_pdf = docx_file.replace('.docx', '.pdf')
    
    output_dir = os.path.dirname(output_pdf) or '.'
    
    try:
        # Use LibreOffice headless mode to convert
        cmd = [
            'soffice',
            '--headless',
            '--convert-to', 'pdf',
            '--outdir', output_dir,
            docx_file
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode == 0:
            # LibreOffice might add .pdf suffix
            expected_pdf = os.path.join(output_dir, os.path.basename(docx_file).replace('.docx', '.pdf'))
            if os.path.exists(expected_pdf):
                print(f"✓ PDF created: {expected_pdf}")
                return expected_pdf
            else:
                print(f"⚠ Expected PDF not found at {expected_pdf}")
                return None
        else:
            print(f"✗ LibreOffice conversion failed: {result.stderr}")
            return None
    
    except FileNotFoundError:
        print("✗ LibreOffice not found. Trying alternative method...")
        return convert_via_windows_api(docx_file, output_pdf)
    except Exception as e:
        print(f"✗ Conversion error: {e}")
        return None

def convert_via_windows_api(docx_file, output_pdf):
    """Fallback: Try Windows COM API via pywin32"""
    try:
        import win32com.client
        
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        
        doc = word.Documents.Open(os.path.abspath(docx_file))
        doc.SaveAs(os.path.abspath(output_pdf), FileFormat=17)  # 17 = PDF format
        doc.Close()
        word.Quit()
        
        print(f"✓ PDF created via Windows API: {output_pdf}")
        return output_pdf
    except Exception as e:
        print(f"✗ Windows API conversion failed: {e}")
        return None

def create_mermaid_script(diagram_name, mermaid_code):
    """Create Mermaid diagram export script"""
    script = f"""
const {{ mermaid, render }} = require('mermaid');

mermaid.initialize({{ 
    startOnLoad: true,
    theme: 'default',
    securityLevel: 'loose'
}});

const svg = await mermaid.render('diagram_{diagram_name}', `{mermaid_code}`);
console.log(svg);
"""
    return script

def export_diagrams_for_arxiv():
    """
    Export Mermaid diagrams as high-resolution PNG (2400x1600px) for ArXiv.
    Since rendering requires Node.js/Mermaid CLI, this provides instructions.
    """
    diagrams = {
        "Figure1_Evolution": """flowchart LR
    subgraph Traditional["Traditional AI"]
        A1["AI Model"] --> A2["Recommendation"]
        A2 --> A3["Human Decision"]
        A3 --> A4["Execution"]
    end
    
    subgraph Autonomous["Autonomous AI (Current Problem)"]
        B1["AI Agent"] --> B2["Decision"]
        B2 --> B3["Execution"]
        B2 --> Gap["❌ No Governance"]
    end
    
    subgraph GlassBox_Solution["With GlassBox"]
        C1["AI Agent"] --> C2["Decision"]
        C2 --> C3["Governance Layer"]
        C3 --> C4["Policy Enforcement"]
        C3 --> C5["Risk Detection"]
        C3 --> C6["Audit Logging"]
        C4 --> C7["Execution (Safe)"]
        C5 --> C7
        C6 --> C7
    end""",
        
        "Figure2_Architecture": """flowchart TB
    subgraph Agents["AI Agents"]
        A1["Agent 1 (Pricing)"]
        A2["Agent 2 (Fraud)"]
        A3["Agent N"]
    end
    
    subgraph Governance["Decision Governance Layer"]
        G1["1. Contract Validation"]
        G2["2. Authorization"]
        G3["3. Policy Evaluation"]
        G4["4. Anomaly Detection"]
        G5["5. Risk Quantification"]
        G6["6. Disposition"]
        G7["7. Velocity Breaker"]
    end
    
    subgraph Systems["Enterprise Systems"]
        S1["Financial Systems"]
        S2["Compliance Systems"]
        S3["Audit Log"]
    end
    
    A1 --> G1 --> G2 --> G3 --> G4 --> G5 --> G6 --> G7 --> S1
    A2 --> G1
    A3 --> G1
    G7 --> S2
    G7 --> S3""",
        
        "Figure3_Pipeline": """flowchart LR
    Stage1["1. Validate<br/>Schema & Payload"]
    Stage2["2. Authorize<br/>Agent Permission"]
    Stage3["3. Evaluate<br/>Policies"]
    Stage4["4. Detect<br/>Anomalies"]
    Stage5["5. Quantify<br/>Risk"]
    Stage6["6. Determine<br/>Disposition"]
    Stage7["7. Check<br/>Velocity"]
    Stage8["8. Audit<br/>Async"]
    Stage9["9. Notify<br/>Stakeholders"]
    
    Stage1 --> Stage2 --> Stage3 --> Stage4 --> Stage5 --> Stage6 --> Stage7 --> Stage8 --> Stage9
    
    FailBlock["Block Decision<br/>Return Error"]
    Stage1 -.->|Invalid| FailBlock
    Stage2 -.->|Unauthorized| FailBlock
    Stage3 -.->|Policy Violation| FailBlock
    Stage7 -.->|Rate Exceeded| FailBlock""",
        
        "Figure4_Sequence": """sequenceDiagram
    participant Agent as AI Agent
    participant Gov as Governance Layer
    participant Policy as Policy Engine
    participant Risk as Risk Evaluator
    participant System as Enterprise System
    
    Agent->>Gov: POST decision
    Gov->>Policy: Evaluate policies
    Policy-->>Gov: Policy action
    Gov->>Risk: Compute risk
    Risk-->>Gov: Risk score
    alt Risk > threshold_block
        Gov->>System: BLOCK
        System-->>Agent: Decision blocked
    else Risk > threshold_review
        Gov->>System: REVIEW
        System-->>Agent: Manual review required
    else
        Gov->>System: APPROVE
        System-->>Agent: Decision approved
        System->>System: Execute
    end""",
        
        "Figure5_FailFast": """flowchart TD
    D["Decision D"]
    V1["Schema Valid?"]
    V2["Agent Authorized?"]
    V3["Policy Allows?"]
    V4["Risk Acceptable?"]
    V5["Rate OK?"]
    
    APPROVE["APPROVE"]
    BLOCK["BLOCK<br/>Return immediately"]
    
    D --> V1
    V1 -->|No| BLOCK
    V1 -->|Yes| V2
    V2 -->|No| BLOCK
    V2 -->|Yes| V3
    V3 -->|No| BLOCK
    V3 -->|Yes| V4
    V4 -->|No| BLOCK
    V4 -->|Yes| V5
    V5 -->|No| BLOCK
    V5 -->|Yes| APPROVE
    
    style BLOCK fill:#ff6b6b
    style APPROVE fill:#51cf66""",
        
        "Figure6_RiskCascade": """flowchart LR
    Agent1["Agent 1<br/>Risk: 0.3"]
    Agent2["Agent 2<br/>Risk: 0.5"]
    Agent3["Agent 3<br/>Risk: 0.2"]
    Agent4["Agent 4<br/>Risk: 0.7"]
    
    Aggregator["Risk Aggregator<br/>max(0.3, 0.5, 0.2, 0.7)"]
    
    Result["Aggregate Risk: 0.7<br/>→ REVIEW"]
    
    Agent1 --> Aggregator
    Agent2 --> Aggregator
    Agent3 --> Aggregator
    Agent4 --> Aggregator
    Aggregator --> Result
    
    style Agent4 fill:#ff9999
    style Result fill:#ffcc99"""
    }
    
    # Create diagram export manifest
    manifest = {
        "title": "GlassBox Diagrams for ArXiv Submission",
        "diagrams": [],
        "export_instructions": """
To export high-resolution Mermaid diagrams (2400x1600px) for ArXiv:

1. Install Mermaid CLI: npm install -g mermaid-cli
2. For each diagram below, create a .mmd file with the Mermaid code
3. Export to PNG: mmdc -i diagram.mmd -o diagram.png -s 2.5 (scale factor for 2400x1600)

Alternatively, use online Mermaid editor (https://mermaid.live):
- Copy diagram code into editor
- Right-click → Export → PNG (Choose 2x or 3x scale)
- Save as PNG with 2400x1600px dimensions minimum

All diagrams provided below for easy copy-paste.
"""
    }
    
    # Save each diagram code to file for manual export
    diagrams_dir = r'c:\Akbar\Personal\AI Research Work\glassbox-agentic-governance\docs\Word\Diagrams_MermaidCode'
    os.makedirs(diagrams_dir, exist_ok=True)
    
    for name, code in diagrams.items():
        file_path = os.path.join(diagrams_dir, f"{name}.mmd")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(code)
        
        manifest["diagrams"].append({
            "name": name,
            "file": f"{name}.mmd",
            "size_recommended": "2400x1600px",
            "format": "PNG",
            "use_case": "ArXiv submission"
        })
        
        print(f"✓ Diagram code saved: {file_path}")
    
    # Save manifest
    manifest_path = os.path.join(diagrams_dir, "export_manifest.json")
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2)
    
    print(f"\n✓ Diagram export manifest created: {manifest_path}")
    print(f"\n📋 INSTRUCTIONS FOR DIAGRAM EXPORT:")
    print(manifest["export_instructions"])
    
    return diagrams_dir

if __name__ == '__main__':
    # Convert DOCX to PDF
    word_file = r'c:\Akbar\Personal\AI Research Work\glassbox-agentic-governance\docs\Word\GlassBox_Publication_Ready_v2_0.docx'
    pdf_file = r'c:\Akbar\Personal\AI Research Work\glassbox-agentic-governance\docs\Word\GlassBox_Publication_Ready_v2_0.pdf'
    
    print("=" * 60)
    print("STEP 1: Converting Word to PDF...")
    print("=" * 60)
    convert_docx_to_pdf(word_file, pdf_file)
    
    print("\n" + "=" * 60)
    print("STEP 2: Exporting Mermaid Diagrams (High-Res)...")
    print("=" * 60)
    export_diagrams_for_arxiv()
    
    print("\n" + "=" * 60)
    print("✅ CONVERSION COMPLETE")
    print("=" * 60)
    print(f"📄 Word: {word_file}")
    print(f"📕 PDF:  {pdf_file}")
    print(f"🎨 Diagrams code: {r'c:\Akbar\Personal\AI Research Work\glassbox-agentic-governance\docs\Word\Diagrams_MermaidCode'}")
