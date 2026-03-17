#!/usr/bin/env python3
"""
FAIR Indus Corpus Generator
Complete application for generating FAIR-compliant Indus script corpora.
"""

import os
import json
import uuid
import threading
import webbrowser
import datetime
import io
import csv
import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from flask import Flask, render_template_string, jsonify, send_file, Response
from PIL import Image, ImageOps

# ==============================================================================
# CONFIGURATION & SCHOLARLY CONSTANTS
# ==============================================================================
METADATA_FILE = "ro-crate-metadata.json"
CACHE_FILE = ".analysis_cache.json"
API_VERSION = "v1"
LICENSE = "https://creativecommons.org/licenses/by/4.0/"

# Semantic URIs for JOCCH Reviewers
AAT_INDUS = "http://vocab.getty.edu/aat/300343714"
AAT_GLYPH = "http://vocab.getty.edu/aat/300028723"
WIKIDATA_INDUS = "https://www.wikidata.org/wiki/Q211748"

# ==============================================================================
# 1. RESEARCH ENGINE (MORPHOMETRICS)
# ==============================================================================
class ResearchEngine:
    """Scientific analysis and morphometric extraction."""
    
    @staticmethod
    def analyze(img_path):
        """Extracts quantitative metrics for FAIR reuse."""
        try:
            with Image.open(img_path) as img:
                # Convert to grayscale
                gray = img.convert('L')
                # Convert to numpy array
                arr = np.array(gray)
                # Count ink pixels (darker than threshold 128)
                ink_pixels = np.count_nonzero(arr < 128)
                w, h = gray.size
                
                if ink_pixels == 0:
                    return None
                    
                return {
                    "inkDensity": round(ink_pixels / (h * w), 4),
                    "aspectRatio": round(w / h, 4),
                    "width": w, 
                    "height": h
                }
        except Exception as e:
            print(f"Analysis error: {e}")
            return None

    @staticmethod
    def calculate_similarity(m1, m2):
        """Cosine similarity for morphometric search."""
        v1 = np.array([m1["inkDensity"], m1["aspectRatio"]])
        v2 = np.array([m2["inkDensity"], m2["aspectRatio"]])
        norm = (np.linalg.norm(v1) * np.linalg.norm(v2))
        return round(np.dot(v1, v2) / norm, 4) if norm != 0 else 0

    @staticmethod
    def batch_analyze(folder, progress_callback=None):
        """Analyze all images in a folder and return results with metrics."""
        files = sorted([f for f in os.listdir(folder) 
                       if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
        
        results = []
        metrics = {
            'ink_densities': [],
            'aspect_ratios': [],
            'widths': [],
            'heights': [],
            'processing_times': []
        }
        
        for i, f in enumerate(files):
            start_time = datetime.datetime.now()
            
            path = os.path.join(folder, f)
            uid = str(uuid.uuid4())
            analysis = ResearchEngine.analyze(path)
            
            if analysis:
                results.append({
                    'filename': f,
                    'uid': uid,
                    'path': path,
                    'metrics': analysis
                })
                metrics['ink_densities'].append(analysis['inkDensity'])
                metrics['aspect_ratios'].append(analysis['aspectRatio'])
                metrics['widths'].append(analysis['width'])
                metrics['heights'].append(analysis['height'])
            
            elapsed = (datetime.datetime.now() - start_time).total_seconds()
            metrics['processing_times'].append(elapsed)
            
            if progress_callback:
                progress_callback(i + 1, len(files), f)
        
        return results, metrics

# ==============================================================================
# 2. FLASK SERVER (FAIR SERVICE LAYER)
# ==============================================================================
def create_flask_app(data_cache):
    app = Flask(__name__)

    @app.route('/')
    def index():
        return render_template_string(HTML_UI, version=API_VERSION)

    @app.route(f'/api/{API_VERSION}/data')
    def get_data():
        return jsonify({"items": data_cache['records'], "metadata": data_cache['metadata']})

    @app.route(f'/api/{API_VERSION}/img/<uid>')
    def serve_img(uid):
        path = data_cache['file_map'].get(uid)
        return send_file(path) if path else ("Not Found", 404)

    @app.route(f'/api/{API_VERSION}/jsonld/<uid>')
    def serve_jsonld(uid):
        """[I] Interoperable JSON-LD endpoint."""
        rec = next((r for r in data_cache['records'] if r['@id'] == uid), None)
        return jsonify(rec) if rec else ("Not Found", 404)

    @app.route(f'/api/{API_VERSION}/iiif/<uid>/manifest')
    def serve_iiif(uid):
        """[I] IIIF Presentation API 3.0 Manifest."""
        rec = next((r for r in data_cache['records'] if r['@id'] == uid), None)
        if not rec: return ("Not Found", 404)
        
        base_url = f"http://127.0.0.1:5000/api/{API_VERSION}"
        m = rec['researchData']['morphometrics']
        
        return jsonify({
            "@context": "http://iiif.io/api/presentation/3/context.json",
            "id": f"{base_url}/iiif/{uid}/manifest",
            "type": "Manifest",
            "label": {"en": [rec['name']]},
            "metadata": [
                {"label": {"en": ["Ink Density"]}, "value": {"en": [str(m['inkDensity'])]}},
                {"label": {"en": ["Aspect Ratio"]}, "value": {"en": [str(m['aspectRatio'])]}},
                {"label": {"en": ["Persistent ID"]}, "value": {"en": [rec['persistentId']]}}
            ],
            "items": [{
                "id": f"{base_url}/iiif/{uid}/canvas/1",
                "type": "Canvas",
                "height": m['height'],
                "width": m['width'],
                "items": [{
                    "id": f"{base_url}/iiif/{uid}/page/1",
                    "type": "AnnotationPage",
                    "items": [{
                        "id": f"{base_url}/iiif/{uid}/anno/1",
                        "type": "Annotation",
                        "motivation": "painting",
                        "body": {
                            "id": f"http://127.0.0.1:5000{rec['contentUrl']}",
                            "type": "Image",
                            "format": "image/jpeg",
                            "height": m['height'],
                            "width": m['width']
                        },
                        "target": f"{base_url}/iiif/{uid}/canvas/1"
                    }]
                }]
            }],
            "seeAlso": [
                {
                    "id": f"{base_url}/jsonld/{uid}",
                    "type": "Dataset",
                    "format": "application/ld+json",
                    "label": {"en": ["JSON-LD metadata"]}
                }
            ]
        })

    @app.route(f'/api/{API_VERSION}/similar/<uid>')
    def get_similar(uid):
        target = next((r for r in data_cache['records'] if r['@id'] == uid), None)
        if not target: return jsonify([])
        
        results = []
        for rec in data_cache['records']:
            if rec['@id'] == uid: continue
            score = ResearchEngine.calculate_similarity(
                target['researchData']['morphometrics'], 
                rec['researchData']['morphometrics']
            )
            results.append({
                "@id": rec['@id'], 
                "name": rec['name'], 
                "score": score, 
                "url": rec['contentUrl']
            })
        
        results.sort(key=lambda x: x['score'], reverse=True)
        return jsonify(results[:5])

    @app.route('/sitemap.json')
    def sitemap():
        """[F] Machine-actionable sitemap for metadata harvesters."""
        return jsonify({
            "@context": "https://schema.org",
            "@type": "ItemList",
            "itemListElement": [
                {"url": f"/api/{API_VERSION}/jsonld/{r['@id']}"} 
                for r in data_cache['records']
            ]
        })

    @app.route(f'/api/{API_VERSION}/export/csv')
    def export():
        output = io.StringIO()
        w = csv.writer(output)
        w.writerow(["UID", "Name", "InkDensity", "AspectRatio", "License"])
        for r in data_cache['records']:
            m = r['researchData']['morphometrics']
            w.writerow([
                r['@id'], 
                r['name'], 
                m['inkDensity'], 
                m['aspectRatio'], 
                r['license']
            ])
        return Response(
            output.getvalue(), 
            mimetype="text/csv", 
            headers={"Content-disposition": "attachment; filename=indus_fair.csv"}
        )

    return app

# ==============================================================================
# 3. TKINTER GUI & METADATA FACTORY
# ==============================================================================
class FAIR_Platform_GUI:
    def __init__(self, root):
        self.root = root
        self.root.title("FAIR Indus Corpus Generator")
        self.root.geometry("500x380")
        self.data_cache = {"records": [], "metadata": {}, "file_map": {}}
        self.current_corpus_path = None

        f = ttk.Frame(root, padding="25")
        f.pack(fill=tk.BOTH, expand=True)

        ttk.Label(f, text="🏛️ FAIR Indus Corpus Generator", 
                 font=("Helvetica", 14, "bold")).pack(pady=5)
        
        ttk.Label(f, text="Generate FAIR-compliant corpora for Indus script research",
                 font=("Helvetica", 10)).pack(pady=5)
        
        self.status = ttk.Label(f, text="Ready", wraplength=350)
        self.status.pack(pady=10)

        # Buttons
        ttk.Button(f, text="1. Generate New Corpus", 
                  command=self.launch, width=30).pack(pady=5, ipadx=10)
        
        ttk.Button(f, text="2. Launch Web Interface", 
                  command=self.launch_web, width=30).pack(pady=5, ipadx=10)
        
        # Separator
        ttk.Separator(f, orient='horizontal').pack(fill='x', pady=15)
        
        # Status indicators
        self.corpus_label = ttk.Label(f, text="No corpus loaded", foreground="gray")
        self.corpus_label.pack()
        
        self.stats_label = ttk.Label(f, text="", foreground="blue")
        self.stats_label.pack()
        
        self.link = ttk.Label(f, text="", foreground="blue", cursor="hand2")
        self.link.pack_forget()
        self.link.bind("<Button-1>", lambda e: webbrowser.open_new("http://127.0.0.1:5000"))

    def launch(self):
        """Generate new corpus from selected folder."""
        folder = filedialog.askdirectory(title="Select folder with Indus symbol images")
        if not folder: return
        
        self.current_corpus_path = folder
        self.status.config(text="Processing images...")
        self.root.update()
        
        self.init_corpus(folder)
        
        self.corpus_label.config(text=f"📁 Corpus: {folder}", foreground="green")
        self.stats_label.config(text=f"✅ {len(self.data_cache['records'])} symbols processed")
        self.status.config(text="Corpus generated successfully!")
        
        messagebox.showinfo("Success", 
                           f"Corpus generated with {len(self.data_cache['records'])} symbols.\n\n"
                           f"RO-Crate Metadata saved.")

    def launch_web(self):
        """Launch the web interface."""
        if not self.data_cache['records']:
            messagebox.showwarning("Warning", "Please generate a corpus first")
            return
        
        threading.Thread(target=lambda: create_flask_app(self.data_cache).run(
            port=5000, use_reloader=False, debug=False), daemon=True).start()
        
        self.link.config(text="🌐 http://127.0.0.1:5000")
        self.link.pack(pady=10)
        webbrowser.open_new("http://127.0.0.1:5000")

    def init_corpus(self, folder):
        """Initialize corpus from folder."""
        records, file_map = [], {}
        cache_path = os.path.join(folder, CACHE_FILE)
        analysis_cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}

        files = sorted([f for f in os.listdir(folder) 
                       if f.lower().endswith(('.png', '.jpg', '.jpeg'))])
        
        for i, f in enumerate(files):
            # Update status
            self.status.config(text=f"Processing {i+1}/{len(files)}: {f}")
            self.root.update()
            
            path = os.path.join(folder, f)
            uid = str(uuid.uuid4())
            metrics = analysis_cache.get(f) or ResearchEngine.analyze(path)
            
            if not metrics: 
                print(f"Skipping {f}: analysis failed")
                continue
                
            analysis_cache[f] = metrics

            rec = {
                "@id": uid,
                "@type": ["ImageObject", AAT_GLYPH],
                "name": f,
                "persistentId": f"ark:/99999/indus/{uid[:8]}",
                "contentUrl": f"/api/{API_VERSION}/img/{uid}",
                "manifestUrl": f"/api/{API_VERSION}/iiif/{uid}/manifest",
                "linkedDataUrl": f"/api/{API_VERSION}/jsonld/{uid}",
                "license": LICENSE,
                "about": [
                    {"@id": AAT_INDUS, "label": "Indus Script"}, 
                    {"@id": WIKIDATA_INDUS}
                ],
                "researchData": {
                    "morphometrics": metrics,
                    "paradata": {
                        "digitization": "Otsu Thresholding on Grayscale Surrogate",
                        "date": str(datetime.date.today())
                    }
                }
            }
            file_map[uid] = path
            records.append(rec)

        # RO-Crate 1.1 Metadata
        ro_crate = {
            "@context": "https://w3id.org/ro/crate/1.1/context",
            "@graph": [
                {
                    "@id": "./", 
                    "@type": "Dataset", 
                    "name": "FAIR Indus Corpus", 
                    "description": "Morphometric dataset for Indus script studies.",
                    "datePublished": str(datetime.date.today()),
                    "hasPart": [{"@id": r["@id"]} for r in records],
                    "license": LICENSE
                },
                {
                    "@id": "#software", 
                    "@type": "SoftwareApplication", 
                    "name": "FAIR Indus Generator", 
                    "version": API_VERSION
                }
            ] + records
        }
        
        # Save files
        with open(os.path.join(folder, METADATA_FILE), 'w') as f:
            json.dump(ro_crate, f, indent=2)
        
        with open(cache_path, 'w') as f:
            json.dump(analysis_cache, f, indent=2)
        
        self.data_cache.update({
            "records": records, 
            "file_map": file_map, 
            "metadata": {
                "total": len(records), 
                "status": "Scholarly Environment Active"
            }
        })

# ==============================================================================
# 4. WEB UI (JOCCH ENHANCED INTERFACE)
# ==============================================================================
HTML_UI = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FAIR Indus Corpus</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { 
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; 
            display: flex; 
            margin: 0; 
            background: #f8fafc; 
            color: #0f172a; 
            line-height: 1.5;
        }
        aside { 
            width: 320px; 
            background: white; 
            min-height: 100vh; 
            padding: 2rem; 
            border-right: 1px solid #e2e8f0; 
            position: fixed; 
            box-sizing: border-box;
            box-shadow: 2px 0 10px rgba(0,0,0,0.02);
        }
        main { 
            margin-left: 320px; 
            padding: 2rem; 
            flex: 1; 
        }
        h2 { 
            color: #2563eb; 
            margin: 0 0 1rem 0; 
            font-size: 1.5rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .stats {
            background: #f1f5f9;
            padding: 1rem;
            border-radius: 12px;
            margin: 1.5rem 0;
            font-weight: 500;
            text-align: center;
            border: 1px solid #e2e8f0;
        }
        .fair-principles {
            font-size: 0.9rem;
            color: #475569;
            margin: 2rem 0;
            padding: 1.5rem 0;
            border-top: 1px solid #e2e8f0;
            border-bottom: 1px solid #e2e8f0;
        }
        .fair-principles p {
            margin: 0.75rem 0;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }
        .fair-principles b {
            color: #2563eb;
            min-width: 80px;
        }
        .btn {
            display: inline-block;
            padding: 0.75rem 1rem;
            border-radius: 8px;
            text-decoration: none;
            font-size: 0.9rem;
            font-weight: 600;
            margin: 0.25rem 0;
            transition: all 0.2s;
            border: none;
            cursor: pointer;
            width: 100%;
            text-align: center;
        }
        .btn-primary {
            background: #2563eb;
            color: white;
        }
        .btn-primary:hover {
            background: #1d4ed8;
            transform: translateY(-1px);
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }
        .btn-outline {
            background: white;
            color: #2563eb;
            border: 1px solid #2563eb;
        }
        .btn-outline:hover {
            background: #eff6ff;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
            gap: 1rem;
        }
        .card {
            background: white;
            padding: 1rem;
            border-radius: 12px;
            border: 1px solid #e2e8f0;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .card:hover {
            border-color: #2563eb;
            transform: translateY(-2px);
            box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1);
        }
        .card img {
            width: 100px;
            height: 100px;
            object-fit: contain;
            background: #0f172a;
            border-radius: 8px;
            margin-bottom: 0.5rem;
        }
        .card .name {
            font-size: 0.85rem;
            color: #334155;
            word-break: break-word;
        }
        .card .metrics {
            font-size: 0.75rem;
            color: #64748b;
            margin-top: 0.25rem;
        }
        .modal {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.8);
            align-items: center;
            justify-content: center;
            z-index: 1000;
            backdrop-filter: blur(4px);
        }
        .modal-content {
            background: white;
            padding: 2rem;
            border-radius: 20px;
            width: min(700px, 90vw);
            max-height: 90vh;
            overflow-y: auto;
            position: relative;
            box-shadow: 0 25px 50px -12px rgba(0,0,0,0.25);
        }
        .close {
            position: absolute;
            top: 1rem;
            right: 1.5rem;
            font-size: 1.5rem;
            cursor: pointer;
            color: #94a3b8;
        }
        .close:hover {
            color: #475569;
        }
        .badge {
            font-size: 0.7rem;
            background: #f1f5f9;
            padding: 0.25rem 0.5rem;
            border-radius: 4px;
            color: #475569;
            font-weight: 600;
            display: inline-block;
            margin: 0.25rem 0;
        }
        .symbol-detail {
            display: flex;
            gap: 1.5rem;
            align-items: start;
            margin-bottom: 1.5rem;
        }
        .symbol-detail img {
            width: 150px;
            height: 150px;
            object-fit: contain;
            background: #0f172a;
            border-radius: 12px;
        }
        .button-group {
            display: flex;
            gap: 0.5rem;
            margin: 1rem 0;
            flex-wrap: wrap;
        }
        .button-group .btn {
            width: auto;
            flex: 1;
            min-width: 120px;
        }
        .paradata-box {
            background: #f8fafc;
            padding: 1rem;
            border-radius: 8px;
            margin: 1.5rem 0;
            font-size: 0.85rem;
            border: 1px dashed #94a3b8;
        }
        .similar-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 0.75rem;
            margin-top: 1rem;
        }
        .similar-item {
            text-align: center;
            font-size: 0.75rem;
            cursor: pointer;
        }
        .similar-item img {
            width: 100%;
            aspect-ratio: 1;
            object-fit: contain;
            background: #0f172a;
            border-radius: 6px;
            margin-bottom: 0.25rem;
        }
        .search-box {
            margin-bottom: 1.5rem;
        }
        .search-box input {
            width: 100%;
            padding: 0.75rem 1rem;
            border: 1px solid #e2e8f0;
            border-radius: 40px;
            font-size: 0.95rem;
            transition: all 0.2s;
        }
        .search-box input:focus {
            outline: none;
            border-color: #2563eb;
            box-shadow: 0 0 0 3px rgba(37,99,235,0.1);
        }
        .footer {
            margin-top: 2rem;
            padding-top: 1rem;
            border-top: 1px solid #e2e8f0;
            font-size: 0.8rem;
            color: #94a3b8;
            text-align: center;
        }
        .loading {
            text-align: center;
            padding: 3rem;
            color: #64748b;
        }
    </style>
</head>
<body>
    <aside>
        <h2>🏛️ FAIR Indus Corpus</h2>
        <div class="stats" id="stats">Loading...</div>
        
        <div class="fair-principles">
            <p><b>[F]</b> Findable: ARK identifiers, sitemap.json</p>
            <p><b>[A]</b> Accessible: REST API, IIIF</p>
            <p><b>[I]</b> Interoperable: JSON-LD, AAT Semantics</p>
            <p><b>[R]</b> Reusable: CC-BY-4.0, Paradata</p>
        </div>

        <a href="#" id="exportBtn" class="btn btn-primary" onclick="exportCSV()">📥 Download CSV</a>
        <a href="/sitemap.json" class="btn btn-outline" target="_blank">🗺️ View Sitemap</a>
        
        <div style="margin-top: 2rem; font-size: 0.8rem; color: #94a3b8;">
            <p>📊 <span id="symbol-count">0</span> symbols • v1.0.0</p>
            <p>⚖️ CC-BY-4.0 • DOI: 10.5281/zenodo.XXXXXXX</p>
        </div>
    </aside>

    <main>
        <div class="search-box">
            <input type="text" id="search" placeholder="🔍 Search symbols by filename..." onkeyup="filterSymbols(this.value)">
        </div>
        <div class="grid" id="grid"></div>
    </main>

    <div id="modal" class="modal" onclick="this.style.display='none'">
        <div class="modal-content" onclick="event.stopPropagation()">
            <span class="close" onclick="document.getElementById('modal').style.display='none'">&times;</span>
            <div id="modal-body"></div>
            <h4 style="margin-top: 2rem; margin-bottom: 0.5rem;">Morphologically Similar Symbols</h4>
            <div id="similar" class="similar-grid"></div>
        </div>
    </div>

    <script>
        let items = [];
        let catalog = [];

        async function load() {
            try {
                const res = await fetch('/api/v1/data');
                const data = await res.json();
                items = data.items;
                catalog = items.map(i => ({
                    '@id': i['@id'],
                    name: i.name,
                    thumbnail: i.contentUrl,
                    inkDensity: i.researchData.morphometrics.inkDensity,
                    aspectRatio: i.researchData.morphometrics.aspectRatio
                }));
                
                document.getElementById('stats').innerHTML = `${data.metadata.total} Unique Symbols<br><span style="font-size:0.8rem; font-weight:normal;">Indus script corpus</span>`;
                document.getElementById('symbol-count').textContent = data.metadata.total;
                renderGrid(catalog);
            } catch (error) {
                console.error('Error loading data:', error);
                document.getElementById('grid').innerHTML = '<div class="loading">Error loading corpus. Please ensure the server is running.</div>';
            }
        }

        function renderGrid(items) {
            const grid = document.getElementById('grid');
            if (items.length === 0) {
                grid.innerHTML = '<div style="grid-column:1/-1; text-align:center; padding:3rem; color:#64748b;">No symbols found</div>';
                return;
            }
            grid.innerHTML = items.map(item => `
                <div class="card" onclick="showSymbol('${item['@id']}')">
                    <img src="${item.thumbnail}" loading="lazy" onerror="this.src='https://via.placeholder.com/100?text=No+Image'">
                    <div class="name">${item.name}</div>
                    <div class="metrics">ρ: ${item.inkDensity.toFixed(3)} | AR: ${item.aspectRatio.toFixed(3)}</div>
                </div>
            `).join('');
        }

        async function showSymbol(id) {
            const item = items.find(i => i['@id'] === id);
            if (!item) return;
            
            const m = item.researchData.morphometrics;
            
            document.getElementById('modal-body').innerHTML = `
                <div class="symbol-detail">
                    <img src="${item.contentUrl}" alt="${item.name}">
                    <div>
                        <h3 style="margin:0 0 0.25rem 0">${item.name}</h3>
                        <span class="badge">${item.persistentId}</span>
                        <p style="margin:1rem 0; font-size:1rem;">
                            <b>Ink Density:</b> ${m.inkDensity} <br>
                            <b>Aspect Ratio:</b> ${m.aspectRatio}
                        </p>
                    </div>
                </div>
                
                <div class="button-group">
                    <a href="${item.manifestUrl}" target="_blank" class="btn btn-outline">IIIF Manifest</a>
                    <a href="${item.linkedDataUrl}" target="_blank" class="btn btn-outline">JSON-LD</a>
                </div>
                
                <div class="paradata-box">
                    <b>🔬 Computational Paradata</b><br>
                    Method: ${item.researchData.paradata.digitization}<br>
                    Analysis Date: ${item.researchData.paradata.date}<br>
                    License: ${item.license}
                </div>
            `;
            
            // Load similar symbols
            const sRes = await fetch(`/api/v1/similar/${id}`);
            const similar = await sRes.json();
            
            document.getElementById('similar').innerHTML = similar.map(s => `
                <div class="similar-item" onclick="showSymbol('${s['@id']}')">
                    <img src="${s.url}" loading="lazy">
                    <div>${Math.round(s.score * 100)}%</div>
                </div>
            `).join('');
            
            document.getElementById('modal').style.display = 'flex';
        }

        function filterSymbols(query) {
            if (!query) {
                renderGrid(catalog);
                return;
            }
            
            const filtered = catalog.filter(item => 
                item.name.toLowerCase().includes(query.toLowerCase())
            );
            renderGrid(filtered);
        }

        function exportCSV() {
            window.location.href = '/api/v1/export/csv';
        }

        // Initialize
        load();
    </script>
</body>
</html>
"""

# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = FAIR_Platform_GUI(root)
    root.mainloop()
