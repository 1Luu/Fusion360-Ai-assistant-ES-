import adsk.core, adsk.fusion, adsk.cam, traceback
import http.client
import ssl
import json
import re
import os
import math
import struct
import zlib

handlers = []
historial_chat = []
PROVEEDORES_LISTA = ("gemini", "groq", "openrouter", "claude", "openai", "deepseek")
indice_rotacion = {p: 0 for p in PROVEEDORES_LISTA + ("auto",)}

modelos_gemini_descubiertos = []
keys_sin_saldo_o_invalidas = set()

CMD_ID = "GeminiPaletteBtn_V82_ID"
CMD_NAME = "Asistente Multi-IA CAD"
CMD_DESC = "Muestra u oculta el panel lateral de modelado asistido por IA (v8.2 Macro-Vault)"
PALETTE_ID = "MultiIAChatPalette_V82_ID"

CARPETA_USUARIO = os.path.expanduser("~")
RUTA_PERMANENTE_JSON = os.path.join(CARPETA_USUARIO, ".fusion360_multi_ia_keys.json")
RUTA_MACROS_JSON = os.path.join(CARPETA_USUARIO, ".fusion360_multi_ia_macros.json")
CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
RUTA_LOCAL_JSON = os.path.join(CARPETA_SCRIPT, "api_keys.json")
CARPETA_ICONOS = os.path.join(CARPETA_SCRIPT, "resources", "icono_ia")

MODELOS_GEMINI_BASE = [
    "gemini-flash-latest",
    "gemini-flash-lite-latest"
]

MODELOS_GROQ = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant"
]

MODELOS_OPENROUTER = [
    "openrouter/free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "meta-llama/llama-3.3-70b-instruct:free"
]

# Plantillas iniciales para usar con 0 tokens desde el primer arranque
MACROS_INICIALES = [
    {
        "nombre": "Brida Mecánica (6 pernos)",
        "prompt": "Crea una brida circular base diámetro 100mm espesor 12mm, cuello central diámetro 50mm altura total 35mm, perforación central pasante de 30mm y 6 agujeros de 10mm en círculo de paso de 75mm",
        "codigo": (
            "sk = rootComp.sketches.add(rootComp.xYConstructionPlane)\n"
            "circles = sk.sketchCurves.sketchCircles\n"
            "circles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), u(50))\n"
            "circles.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), u(15))\n"
            "for i in range(6):\n"
            "    ang = i * (2 * math.pi / 6)\n"
            "    px = 37.5 * math.cos(ang)\n"
            "    py = 37.5 * math.sin(ang)\n"
            "    circles.addByCenterRadius(adsk.core.Point3D.create(u(px), u(py), 0), u(5))\n"
            "perfiles_base = adsk.core.ObjectCollection.create()\n"
            "for p in sk.profiles:\n"
            "    if p.areaProperties().area > (math.pi * (u(20)**2)):\n"
            "        perfiles_base.add(p)\n"
            "ext = rootComp.features.extrudeFeatures\n"
            "ext.addSimple(perfiles_base, adsk.core.ValueInput.createByReal(u(12)), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)\n"
            "sk2 = rootComp.sketches.add(rootComp.xYConstructionPlane)\n"
            "c2 = sk2.sketchCurves.sketchCircles\n"
            "c2.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), u(25))\n"
            "c2.addByCenterRadius(adsk.core.Point3D.create(0, 0, 0), u(15))\n"
            "for p in sk2.profiles:\n"
            "    if p.sketchLoops.count == 2:\n"
            "        ext.addSimple(p, adsk.core.ValueInput.createByReal(u(35)), adsk.fusion.FeatureOperations.JoinFeatureOperation)"
        )
    },
    {
        "nombre": "Cubo Base 10x10x10 mm",
        "prompt": "Crea un cubo de 10x10x10 mm en el plano XY",
        "codigo": (
            "sk = rootComp.sketches.add(rootComp.xYConstructionPlane)\n"
            "sk.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(u(10), u(10), 0))\n"
            "prof = sk.profiles.item(0)\n"
            "rootComp.features.extrudeFeatures.addSimple(prof, adsk.core.ValueInput.createByReal(u(10)), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)"
        )
    },
    {
        "nombre": "Empalme 1mm (En selección mouse)",
        "prompt": "Aplica un empalme (fillet) de 1mm a las aristas o caras seleccionadas con el mouse",
        "codigo": (
            "edgeCol = adsk.core.ObjectCollection.create()\n"
            "for e in sel_edges:\n"
            "    edgeCol.add(e)\n"
            "for f in sel_faces:\n"
            "    for e in f.edges:\n"
            "        edgeCol.add(e)\n"
            "if edgeCol.count == 0:\n"
            "    raise Exception('Selecciona primero una o más aristas/caras con el mouse.')\n"
            "fillets = rootComp.features.filletFeatures\n"
            "fInput = fillets.createInput()\n"
            "fInput.addConstantRadiusEdgeSet(edgeCol, adsk.core.ValueInput.createByReal(u(1.0)), True)\n"
            "fillets.add(fInput)"
        )
    }
]


# ==============================================================================
# GESTIÓN DE BIBLIOTECA DE GEOMETRÍAS (0 TOKENS)
# ==============================================================================
def cargar_biblioteca_macros():
    if os.path.exists(RUTA_MACROS_JSON):
        try:
            with open(RUTA_MACROS_JSON, "r", encoding="utf-8") as f:
                datos = json.load(f)
                if isinstance(datos, list) and len(datos) > 0:
                    return datos
        except:
            pass
    guardar_biblioteca_macros(MACROS_INICIALES)
    return list(MACROS_INICIALES)


def guardar_biblioteca_macros(lista_macros):
    try:
        with open(RUTA_MACROS_JSON, "w", encoding="utf-8") as f:
            json.dump(lista_macros, f, indent=2, ensure_ascii=False)
    except:
        pass


def agregar_o_actualizar_macro(nombre, prompt_orig, codigo_py):
    macros = cargar_biblioteca_macros()
    nombre_limpio = nombre.strip()[:45]
    reemplazado = False
    for m in macros:
        if m.get("nombre", "").lower() == nombre_limpio.lower():
            m["nombre"] = nombre_limpio
            m["prompt"] = prompt_orig
            m["codigo"] = codigo_py
            reemplazado = True
            break
    if not reemplazado:
        macros.insert(0, {
            "nombre": nombre_limpio,
            "prompt": prompt_orig,
            "codigo": codigo_py
        })
    guardar_biblioteca_macros(macros)
    return macros


def borrar_macro_por_nombre(nombre):
    macros = cargar_biblioteca_macros()
    macros = [m for m in macros if m.get("nombre") != nombre]
    guardar_biblioteca_macros(macros)
    return macros


# ==============================================================================
# GENERADOR AUTOMÁTICO DE ICONOS
# ==============================================================================
def crear_png_icono(ruta_archivo, tam):
    filas = bytearray()
    centro = (tam - 1) / 2.0
    radio_fondo = tam * 0.44
    radio_estrella = tam * 0.32

    for y in range(tam):
        filas.append(0)
        dy = abs(y - centro)
        for x in range(tam):
            dx = abs(x - centro)
            dist_caja = ((dx / radio_fondo) ** 4 + (dy / radio_fondo) ** 4) ** 0.25
            if dist_caja > 1.0:
                filas.extend((0, 0, 0, 0))
            else:
                dist_est = (dx ** 0.55 + dy ** 0.55)
                limite_est = (radio_estrella ** 0.55)
                if dist_est <= limite_est:
                    filas.extend((255, 255, 255, 255))
                elif dist_est <= limite_est * 1.18:
                    filas.extend((110, 215, 255, 255))
                else:
                    r = 6
                    g = int(125 + (1 - y / float(tam)) * 45)
                    b = int(195 + (1 - y / float(tam)) * 45)
                    filas.extend((r, g, b, 255))

    def chunk_png(tipo, datos):
        crc = zlib.crc32(tipo + datos) & 0xffffffff
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", tam, tam, 8, 6, 0, 0, 0)
    idat = zlib.compress(bytes(filas), 9)
    png_bytes = b"\x89PNG\r\n\x1a\n" + chunk_png(b"IHDR", ihdr) + chunk_png(b"IDAT", idat) + chunk_png(b"IEND", b"")

    with open(ruta_archivo, "wb") as f:
        f.write(png_bytes)


def asegurar_iconos_barra():
    os.makedirs(CARPETA_ICONOS, exist_ok=True)
    for t in (16, 32, 64):
        ruta_png = os.path.join(CARPETA_ICONOS, f"{t}x{t}.png")
        if not os.path.exists(ruta_png):
            crear_png_icono(ruta_png, t)

    ruta_2x = os.path.join(CARPETA_ICONOS, "32x32@2x.png")
    if not os.path.exists(ruta_2x):
        crear_png_icono(ruta_2x, 64)

    svg_plantilla = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
    <rect x="2" y="2" width="28" height="28" rx="6" fill="#0696d7"/>
    <path d="M16 5 C17.5 12.5 19.5 14.5 27 16 C19.5 17.5 17.5 19.5 16 27 C14.5 19.5 12.5 17.5 5 16 C12.5 14.5 14.5 12.5 16 5 Z" fill="#ffffff"/>
    </svg>"""
    for t in (16, 32):
        ruta_svg = os.path.join(CARPETA_ICONOS, f"{t}x{t}.svg")
        if not os.path.exists(ruta_svg):
            with open(ruta_svg, "w", encoding="utf-8") as f:
                f.write(svg_plantilla)

    return CARPETA_ICONOS


# ==============================================================================
# GESTIÓN PERMANENTE DE TOKENS (POOL DE API KEYS)
# ==============================================================================
def normalizar_estructura(datos):
    base = {p: [] for p in PROVEEDORES_LISTA}
    if not isinstance(datos, dict):
        return base
    for prov in base:
        val = datos.get(prov, [])
        if isinstance(val, str):
            limpia = val.strip()
            base[prov] = [limpia] if limpia and not limpia.startswith("gen-lang-client") else []
        elif isinstance(val, list):
            base[prov] = [
                str(k).strip() for k in val
                if str(k).strip() and not str(k).strip().startswith("gen-lang-client")
            ]
    return base


def cargar_pool_keys():
    pool = {p: [] for p in PROVEEDORES_LISTA}
    for ruta in (RUTA_PERMANENTE_JSON, RUTA_LOCAL_JSON):
        if os.path.exists(ruta):
            try:
                with open(ruta, "r", encoding="utf-8") as f:
                    cargado = normalizar_estructura(json.load(f))
                    for p in pool:
                        for k in cargado[p]:
                            if k not in pool[p]:
                                pool[p].append(k)
            except:
                pass

    ruta_txt = os.path.join(CARPETA_SCRIPT, "api_key.txt")
    if os.path.exists(ruta_txt):
        try:
            with open(ruta_txt, "r", encoding="utf-8") as f:
                k_old = f.read().strip()
                if k_old and not k_old.startswith("gen-lang-client") and k_old not in pool["gemini"]:
                    pool["gemini"].append(k_old)
        except:
            pass

    guardar_pool_en_disco(pool)
    return pool


def guardar_pool_en_disco(pool):
    for ruta in (RUTA_PERMANENTE_JSON, RUTA_LOCAL_JSON):
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(pool, f, indent=2)
        except:
            pass


def detectar_proveedor_por_prefijo(key_str, proveedor_actual):
    if proveedor_actual != "auto":
        return proveedor_actual
    if key_str.startswith("gsk_"):
        return "groq"
    if key_str.startswith("sk-or-"):
        return "openrouter"
    if key_str.startswith("sk-ant-"):
        return "claude"
    if key_str.startswith("AIza") or key_str.startswith("AQ."):
        return "gemini"
    return "gemini"


def agregar_keys_proveedor(proveedor, texto_keys):
    global modelos_gemini_descubiertos
    pool = cargar_pool_keys()
    fragmentos = re.split(r"[,;\s\n]+", texto_keys.strip())
    agregadas = 0
    prov_destino = proveedor if proveedor != "auto" else "gemini"

    for frag in fragmentos:
        limpia = frag.strip().replace('"', '').replace("'", "")
        if limpia and not limpia.startswith("gen-lang-client"):
            prov_final = detectar_proveedor_por_prefijo(limpia, proveedor)
            prov_destino = prov_final
            keys_sin_saldo_o_invalidas.discard(limpia)
            if limpia not in pool[prov_final]:
                pool[prov_final].append(limpia)
                agregadas += 1

    modelos_gemini_descubiertos = []
    guardar_pool_en_disco(pool)
    return pool[prov_destino], agregadas, prov_destino


def borrar_keys_proveedor(proveedor):
    pool = cargar_pool_keys()
    if proveedor in pool:
        pool[proveedor] = []
        guardar_pool_en_disco(pool)
    return []


def resumir_pool_proveedor(proveedor):
    pool = cargar_pool_keys()
    if proveedor == "auto":
        total = sum(len(v) for v in pool.values())
        detalle = [f"{p.upper()}:{len(v)}" for p, v in pool.items() if len(v) > 0]
        return f"{total} Keys activas ({', '.join(detalle)})" if total > 0 else "VACIA"

    lista = pool.get(proveedor, [])
    if not lista:
        return "VACIA"
    mascaras = [f"#{i+1}({k[:5]}..{k[-3:]})" for i, k in enumerate(lista)]
    return f"{len(lista)} Key(s): " + " | ".join(mascaras)


# ==============================================================================
# AUTO-DESCUBRIMIENTO DE MODELOS ACTIVOS EN GOOGLE AI STUDIO
# ==============================================================================
def obtener_modelos_gemini_activos(api_key):
    global modelos_gemini_descubiertos
    if modelos_gemini_descubiertos:
        return modelos_gemini_descubiertos

    lista_final = list(MODELOS_GEMINI_BASE)
    conn = None
    try:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection("generativelanguage.googleapis.com", timeout=6, context=ctx)
        cabeceras = {
            "x-goog-api-key": api_key,
            "Connection": "close",
            "User-Agent": "Fusion360CAD/8.2"
        }
        conn.request("GET", "/v1beta/models", headers=cabeceras)
        res = conn.getresponse()
        if res.status == 200:
            data = json.loads(res.read().decode("utf-8"))
            excluidos = ("1.5", "2.0", "tts", "image", "audio", "vision", "embedding", "aqa", "robotics", "computer", "thinking")
            descubiertos_flash = []
            descubiertos_otros = []

            for m in data.get("models", []):
                metodos = m.get("supportedGenerationMethods", [])
                if "generateContent" not in metodos:
                    continue
                nom = m.get("name", "").replace("models/", "")
                nom_low = nom.lower()
                if any(ex in nom_low for ex in excluidos):
                    continue
                if "flash" in nom_low:
                    descubiertos_flash.append(nom)
                elif "gemma-3-27b" in nom_low:
                    descubiertos_otros.append(nom)

            for mod in descubiertos_flash + descubiertos_otros:
                if mod not in lista_final:
                    lista_final.append(mod)

            if len(lista_final) > 2:
                modelos_gemini_descubiertos = lista_final[:5]
                return modelos_gemini_descubiertos
    except:
        pass
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

    return lista_final


# ==============================================================================
# INTERFAZ GRÁFICA HTML/CSS/JS (v8.2 Macro-Vault)
# ==============================================================================
HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
    * { box-sizing: border-box; font-family: 'Segoe UI', Tahoma, sans-serif; }
    body { margin: 0; padding: 10px; background: #22252a; color: #e8eaed; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
    .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 6px; border-bottom: 1px solid #3c4043; font-size: 12px; color: #9aa0a6; gap: 5px; }
    .badge-v82 { background: #004d40; color: #80cbc4; border: 1px solid #26a69a; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; white-space: nowrap; }
    select { background: #181a1d; color: #e8eaed; border: 1px solid #3c4043; border-radius: 4px; padding: 4px 6px; font-size: 11px; flex: 1; outline: none; font-weight: 600; }
    select:focus { border-color: #0696d7; }
    .btn-small { background: #3c4043; color: #e8eaed; border: none; padding: 4px 7px; border-radius: 4px; cursor: pointer; font-size: 11px; white-space: nowrap; }
    .btn-small:hover { background: #5f6368; }
    .key-bar { display: flex; gap: 4px; padding: 6px 0 3px 0; align-items: center; }
    .key-bar input { flex: 1; background: #181a1d; color: #8ab4f8; border: 1px solid #3c4043; border-radius: 4px; padding: 4px 6px; font-size: 11px; outline: none; }
    .key-bar input:focus { border-color: #0696d7; }
    .btn-save { background: #2e7d32; color: white; border: none; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 11px; font-weight: 600; white-space: nowrap; }
    .btn-save:hover { background: #388e3c; }
    .btn-del-key { background: #5c1e1e; color: #ffb3b3; border: none; padding: 4px 6px; border-radius: 4px; cursor: pointer; font-size: 10px; white-space: nowrap; }
    .btn-del-key:hover { background: #7a2828; }
    .status-bar { font-size: 10.5px; color: #8ab4f8; padding-bottom: 5px; border-bottom: 1px solid #3c4043; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

    /* BARRA DE BIBLIOTECA 0-TOKENS */
    .macro-bar { display: flex; gap: 4px; padding: 5px 0; border-bottom: 1px solid #3c4043; align-items: center; }
    .macro-label { font-size: 10.5px; color: #80cbc4; font-weight: 700; white-space: nowrap; }
    .btn-run-macro { background: #00695c; color: #ffffff; border: 1px solid #26a69a; padding: 4px 7px; border-radius: 4px; cursor: pointer; font-size: 10.5px; font-weight: 700; white-space: nowrap; }
    .btn-run-macro:hover { background: #00897b; }
    .btn-load-prompt { background: #3c4043; color: #e8eaed; border: none; padding: 4px 6px; border-radius: 4px; cursor: pointer; font-size: 10.5px; white-space: nowrap; }
    .btn-load-prompt:hover { background: #5f6368; }

    #chat-box { flex: 1; overflow-y: auto; padding: 10px 4px; display: flex; flex-direction: column; gap: 10px; }
    .msg { padding: 9px 12px; border-radius: 8px; max-width: 94%; font-size: 12.5px; line-height: 1.45; word-wrap: break-word; white-space: pre-wrap; }
    .user { background: #0696d7; color: #ffffff; align-self: flex-end; border-bottom-right-radius: 2px; }
    .bot { background: #2d3136; color: #e8eaed; align-self: flex-start; border: 1px solid #3c4043; border-bottom-left-radius: 2px; }
    .wait { background: #3b3018; color: #ffd57e; align-self: flex-start; border: 1px solid #8a6d1b; font-size: 12px; }
    .autofix { background: #3d2210; color: #ffb784; align-self: flex-start; border: 1px solid #b55d19; font-size: 12px; }
    .error { background: #5c1e1e; color: #ffb3b3; align-self: flex-start; border: 1px solid #a83232; font-size: 12px; }
    .welcome-title { color: #8ab4f8; font-weight: 700; font-size: 13px; margin-bottom: 4px; display: block; }
    .divider { border-top: 1px solid #454950; margin: 8px 0 6px 0; }
    .updates-title { color: #9aa0a6; font-weight: 600; font-size: 11.5px; display: block; margin-bottom: 2px; }
    .updates-list { font-size: 11.5px; color: #bdc1c6; margin: 0; padding-left: 15px; line-height: 1.35; }
    .input-area { display: flex; gap: 6px; padding-top: 8px; border-top: 1px solid #3c4043; }
    textarea { flex: 1; background: #181a1d; color: #fff; border: 1px solid #3c4043; border-radius: 6px; padding: 8px; resize: none; height: 52px; font-size: 13px; outline: none; }
    textarea:focus { border-color: #0696d7; }
    button.send { background: #0696d7; color: white; border: none; padding: 0 14px; border-radius: 6px; cursor: pointer; font-weight: 600; }
    button.send:hover { background: #057bb0; }
    button.stop-btn { background: #a83232; color: white; border: none; padding: 0 10px; border-radius: 6px; cursor: pointer; font-weight: 600; display: none; font-size: 12px; }
    button.stop-btn:hover { background: #c93b3b; }

    /* ESTILOS VISOR DE CÓDIGO Y GUARDADO EN BIBLIOTECA */
    .code-container { margin-top: 8px; border-radius: 5px; overflow: hidden; display: none; background: #16181a; border: 1px solid #3c4043; }
    .code-header { background: #202327; padding: 5px 8px; font-size: 10.5px; display: flex; justify-content: space-between; align-items: center; color: #9aa0a6; border-bottom: 1px solid #333; gap: 4px; flex-wrap: wrap; }
    .code-actions { display: flex; gap: 4px; }
    .code-btn { background: #3c4043; color: #fff; border: none; border-radius: 4px; cursor: pointer; padding: 3px 7px; font-size: 10px; font-weight: 600; }
    .code-btn:hover { background: #0696d7; }
    .code-btn-save { background: #00695c; color: #e0f2f1; border: none; border-radius: 4px; cursor: pointer; padding: 3px 7px; font-size: 10px; font-weight: 600; }
    .code-btn-save:hover { background: #00897b; }
    .save-drawer { display: none; background: #1c2526; padding: 6px 8px; border-bottom: 1px solid #2e4446; gap: 4px; align-items: center; }
    .save-drawer input { flex: 1; background: #111516; color: #80cbc4; border: 1px solid #26a69a; border-radius: 4px; padding: 3px 6px; font-size: 11px; outline: none; }
    .code-content { padding: 9px; font-family: 'Consolas', 'Courier New', monospace; font-size: 11px; color: #d4d4d4; white-space: pre-wrap; word-break: break-all; margin: 0; max-height: 220px; overflow-y: auto; }
    .btn-toggle-code { background: transparent; color: #8ab4f8; border: 1px solid #8ab4f8; padding: 3px 8px; border-radius: 4px; cursor: pointer; font-size: 10.5px; margin-top: 7px; display: inline-block; font-weight: 600; }
    .btn-toggle-code:hover { background: rgba(138, 180, 248, 0.15); }
</style>
</head>
<body>
    <div class="header">
        <span class="badge-v82">v8.2 Macro-Vault</span>
        <select id="provider-select" onchange="cambiarProveedor()">
            <option value="gemini" selected>Google Gemini (Flash + Lite)</option>
            <option value="groq">⚡ Groq (Llama 3.3 70B / Gratis)</option>
            <option value="openrouter">🌐 OpenRouter (Modelos :free)</option>
            <option value="auto">🔄 Auto-Balanceo (Todas mis Keys)</option>
            <option value="claude">Anthropic Claude (Sonnet)</option>
            <option value="openai">OpenAI ChatGPT (GPT-4o)</option>
            <option value="deepseek">DeepSeek (DeepSeek-V3)</option>
        </select>
        <button class="btn-small" onclick="limpiarChat()">Limpiar</button>
    </div>
    <div class="key-bar">
        <input type="password" id="api-key-input" placeholder="Añade 1 o varias Keys (separadas por coma)..." />
        <button class="btn-save" onclick="guardarKey()">+ Key</button>
        <button class="btn-del-key" title="Borrar las Keys de este proveedor" onclick="borrarKeys()">✕</button>
    </div>
    <div class="status-bar" id="key-status">Cargando tokens guardados...</div>

    <!-- BARRA DE BIBLIOTECA DE GEOMETRÍAS (0 TOKENS) -->
    <div class="macro-bar">
        <span class="macro-label">📚 0-Tokens:</span>
        <select id="macro-select" title="Geometrías guardadas listas para ejecutar sin gastar tokens"></select>
        <button class="btn-run-macro" title="Dibujar esta pieza inmediatamente sin gastar tokens" onclick="ejecutarMacroSeleccionada()">▶ Crear</button>
        <button class="btn-load-prompt" title="Cargar el prompt en la caja de texto para editar medidas" onclick="cargarPromptMacro()">📝</button>
        <button class="btn-del-key" title="Eliminar pieza seleccionada de la biblioteca" onclick="eliminarMacroSeleccionada()">✕</button>
    </div>

    <div id="chat-box">
        <div class="msg bot" id="welcome-msg"><span class="welcome-title">👋 Cómo usar el Asistente CAD:</span>1. Elige tu IA arriba o ejecuta directo una pieza de la barra <b>📚 0-Tokens</b>.
2. <b>Opcional:</b> Selecciona con el mouse caras o aristas para modificarlas.
3. En cualquier respuesta, abre <b>👁️ Ver Código Python</b> para auditar el script o guardarlo con <b>💾 Guardar 0-Tokens</b>.<div class="divider"></div><span class="updates-title">🚀 Novedades en v8.2 Macro-Vault:</span><ul class="updates-list"><li><b>Biblioteca Local (0 Tokens):</b> Guarda scripts de geometrías complejas y ejecútalos en 0,05s sin gastar cuota de API.</li><li><b>Visor de Código + Copiado Seguro:</b> Audita y copia el código Python generado en cada mensaje.</li><li><b>Salto Anti-503 + Auto-Fix:</b> Rotación automática hacia <i>flash-lite</i>, Groq y OpenRouter.</li></ul></div>
    </div>
    <div class="input-area">
        <textarea id="prompt" placeholder="Selecciona algo con el mouse o escribe qué modelar..."></textarea>
        <button class="stop-btn" id="btn-cancelar" onclick="cancelarActual()">⏹</button>
        <button class="send" onclick="encolarPedido()">Enviar</button>
    </div>

    <script>
        const chatBox = document.getElementById('chat-box');
        const promptInput = document.getElementById('prompt');
        const keyInput = document.getElementById('api-key-input');
        const providerSelect = document.getElementById('provider-select');
        const keyStatus = document.getElementById('key-status');
        const macroSelect = document.getElementById('macro-select');
        const btnCancelar = document.getElementById('btn-cancelar');
        const HTML_BIENVENIDA = document.getElementById('welcome-msg').innerHTML;

        let colaPedidos = [];
        let procesandoCola = false;
        let canceladoPorUsuario = false;
        let listaMacrosCache = [];

        const EJEMPLOS_KEY = {
            gemini: 'Pega Key de Gemini (AIzaSy... o AQ.Ab8...)',
            groq: 'Pega Key gratuita de Groq (gsk_...)',
            openrouter: 'Pega Key gratuita de OpenRouter (sk-or-v1-...)',
            auto: 'Pega cualquier Key (detecta el proveedor sola)...',
            claude: 'Pega Key de Claude (sk-ant-...)',
            openai: 'Pega Key de OpenAI (sk-proj-...)',
            deepseek: 'Pega Key de DeepSeek (sk-...)'
        };

        function pausaUI(ms) {
            return new Promise(r => setTimeout(r, ms));
        }

        function consultarEstadoKey() {
            const prov = providerSelect.value;
            keyInput.placeholder = EJEMPLOS_KEY[prov] || 'Añade 1 o varias Keys...';
            adsk.fusionSendData('estado_key', JSON.stringify({ proveedor: prov })).then(res => {
                keyInput.value = '';
                if (res && res !== 'VACIA') {
                    keyStatus.textContent = '🔑 ' + res;
                } else {
                    keyStatus.textContent = '⚠️ Sin Keys en ' + prov.toUpperCase() + '. Pega una arriba y dale a + Key.';
                }
            });
        }

        function actualizarMenuMacros(macros) {
            listaMacrosCache = macros || [];
            macroSelect.innerHTML = '';
            if (listaMacrosCache.length === 0) {
                const opt = document.createElement('option');
                opt.value = '-1';
                opt.textContent = '(Biblioteca vacía)';
                macroSelect.appendChild(opt);
                return;
            }
            listaMacrosCache.forEach((m, idx) => {
                const opt = document.createElement('option');
                opt.value = String(idx);
                opt.textContent = m.nombre;
                macroSelect.appendChild(opt);
            });
        }

        function cargarMacrosIniciales() {
            adsk.fusionSendData('listar_macros', '{}').then(res => {
                try {
                    actualizarMenuMacros(JSON.parse(res));
                } catch (e) {}
            });
        }

        setTimeout(() => {
            consultarEstadoKey();
            cargarMacrosIniciales();
        }, 300);

        function cambiarProveedor() {
            consultarEstadoKey();
        }

        function agregarMensaje(texto, clase) {
            const div = document.createElement('div');
            div.className = 'msg ' + clase;
            div.textContent = texto;
            chatBox.appendChild(div);
            chatBox.scrollTop = chatBox.scrollHeight;
            return div;
        }

        function cancelarActual() {
            canceladoPorUsuario = true;
        }

        function copiarTextoSeguro(texto, btnElem) {
            const ta = document.createElement('textarea');
            ta.value = texto;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            try {
                document.execCommand('copy');
                btnElem.textContent = '¡Copiado!';
            } catch (e) {
                if (navigator.clipboard) navigator.clipboard.writeText(texto);
                btnElem.textContent = '¡Copiado!';
            }
            document.body.removeChild(ta);
            setTimeout(() => { btnElem.textContent = '📋 Copiar'; }, 1800);
        }

        function inyectarBotonCodigo(burbujaElem, codigoPython, promptOriginal, isError) {
            const btnToggle = document.createElement('button');
            btnToggle.className = 'btn-toggle-code';

            if (isError) {
                btnToggle.style.color = '#ffb3b3';
                btnToggle.style.borderColor = '#ffb3b3';
                btnToggle.textContent = '👁️ Ver Código Fallido';
            } else {
                btnToggle.textContent = '👁️ Ver Código Python';
            }

            const codeCont = document.createElement('div');
            codeCont.className = 'code-container';

            const codeHead = document.createElement('div');
            codeHead.className = 'code-header';

            const titleSpan = document.createElement('span');
            titleSpan.textContent = '🐍 Script Python API';

            const actionsDiv = document.createElement('div');
            actionsDiv.className = 'code-actions';

            const btnCopy = document.createElement('button');
            btnCopy.className = 'code-btn';
            btnCopy.textContent = '📋 Copiar';
            btnCopy.onclick = function() {
                copiarTextoSeguro(codigoPython, btnCopy);
            };

            const btnSaveMacro = document.createElement('button');
            btnSaveMacro.className = 'code-btn-save';
            btnSaveMacro.textContent = '💾 Guardar 0-Tokens';

            actionsDiv.appendChild(btnCopy);
            if (!isError) {
                actionsDiv.appendChild(btnSaveMacro);
            }

            codeHead.appendChild(titleSpan);
            codeHead.appendChild(actionsDiv);

            // Cajón inline para nombrar y guardar la geometría sin usar popups bloqueables
            const saveDrawer = document.createElement('div');
            saveDrawer.className = 'save-drawer';

            const inputNombre = document.createElement('input');
            inputNombre.type = 'text';
            inputNombre.placeholder = 'Nombre de la pieza (ej. Engranaje Z20)...';
            inputNombre.value = (promptOriginal || 'Pieza personalizada').slice(0, 34);

            const btnConfirmSave = document.createElement('button');
            btnConfirmSave.className = 'btn-run-macro';
            btnConfirmSave.textContent = 'Guardar';
            btnConfirmSave.onclick = function() {
                const nom = inputNombre.value.trim();
                if (!nom) return;
                adsk.fusionSendData('guardar_macro', JSON.stringify({
                    nombre: nom,
                    prompt: promptOriginal || nom,
                    codigo: codigoPython
                })).then(res => {
                    const datos = JSON.parse(res);
                    actualizarMenuMacros(datos.macros);
                    saveDrawer.style.display = 'none';
                    btnSaveMacro.textContent = '✅ ¡Guardado!';
                    setTimeout(() => { btnSaveMacro.textContent = '💾 Guardar 0-Tokens'; }, 2000);
                });
            };

            saveDrawer.appendChild(inputNombre);
            saveDrawer.appendChild(btnConfirmSave);

            btnSaveMacro.onclick = function() {
                saveDrawer.style.display = saveDrawer.style.display === 'flex' ? 'none' : 'flex';
                if (saveDrawer.style.display === 'flex') inputNombre.focus();
            };

            const codePre = document.createElement('pre');
            codePre.className = 'code-content';
            if (isError) codePre.style.color = '#ff9999';
            codePre.textContent = codigoPython;

            codeCont.appendChild(codeHead);
            codeCont.appendChild(saveDrawer);
            codeCont.appendChild(codePre);

            btnToggle.onclick = function() {
                if (codeCont.style.display === 'block') {
                    codeCont.style.display = 'none';
                    btnToggle.textContent = isError ? '👁️ Ver Código Fallido' : '👁️ Ver Código Python';
                } else {
                    codeCont.style.display = 'block';
                    btnToggle.textContent = '🙈 Ocultar Código';
                    chatBox.scrollTop = chatBox.scrollHeight;
                }
            };

            burbujaElem.appendChild(document.createElement('br'));
            burbujaElem.appendChild(btnToggle);
            burbujaElem.appendChild(codeCont);
        }

        // ==============================================================================
        // FUNCIONES DE LA BIBLIOTECA 0-TOKENS
        // ==============================================================================
        async function ejecutarMacroSeleccionada() {
            const idx = parseInt(macroSelect.value, 10);
            if (isNaN(idx) || idx < 0 || idx >= listaMacrosCache.length) return;

            const macro = listaMacrosCache[idx];
            agregarMensaje('⚡ [Biblioteca 0-Tokens]: ' + macro.nombre, 'user');
            const burbuja = agregarMensaje('🔨 Dibujando desde biblioteca local (0 tokens)...', 'bot');

            await pausaUI(30);
            try {
                const execRaw = await adsk.fusionSendData('ejecutar_codigo', JSON.stringify({
                    codigo: macro.codigo,
                    mensaje_usuario: 'INSTRUCCIÓN DEL USUARIO: ' + (macro.prompt || macro.nombre),
                    etiqueta: '📚 Biblioteca 0-Tokens'
                }));
                const execRes = JSON.parse(execRaw);
                burbuja.className = 'msg ' + (execRes.exito ? 'bot' : 'error');
                burbuja.textContent = '';

                const txtSpan = document.createElement('span');
                txtSpan.textContent = execRes.exito ? execRes.mensaje : ('❌ Error al ejecutar macro:\n' + execRes.error_corto);
                burbuja.appendChild(txtSpan);

                inyectarBotonCodigo(burbuja, macro.codigo, macro.prompt, !execRes.exito);
            } catch (e) {
                burbuja.className = 'msg error';
                burbuja.textContent = '❌ Error al ejecutar macro: ' + String(e);
            }
            chatBox.scrollTop = chatBox.scrollHeight;
        }

        function cargarPromptMacro() {
            const idx = parseInt(macroSelect.value, 10);
            if (isNaN(idx) || idx < 0 || idx >= listaMacrosCache.length) return;
            promptInput.value = listaMacrosCache[idx].prompt || listaMacrosCache[idx].nombre;
            promptInput.focus();
        }

        function eliminarMacroSeleccionada() {
            const idx = parseInt(macroSelect.value, 10);
            if (isNaN(idx) || idx < 0 || idx >= listaMacrosCache.length) return;
            const nom = listaMacrosCache[idx].nombre;
            adsk.fusionSendData('borrar_macro', JSON.stringify({ nombre: nom })).then(res => {
                const datos = JSON.parse(res);
                actualizarMenuMacros(datos.macros);
                agregarMensaje('🗑️ Se eliminó "' + nom + '" de tu biblioteca 0-Tokens.', 'bot');
            });
        }

        function cuentaRegresiva(segundos, motivo, burbuja) {
            return new Promise(resolve => {
                let restantes = Math.max(1, Math.ceil(segundos));
                burbuja.className = 'msg wait';
                burbuja.textContent = '⏳ ' + motivo + '\nReintentando automáticamente en ' + restantes + 's...';
                chatBox.scrollTop = chatBox.scrollHeight;

                const timer = setInterval(() => {
                    if (canceladoPorUsuario) {
                        clearInterval(timer);
                        resolve();
                        return;
                    }
                    restantes--;
                    if (restantes <= 0) {
                        clearInterval(timer);
                        resolve();
                    } else {
                        burbuja.textContent = '⏳ ' + motivo + '\nReintentando automáticamente en ' + restantes + 's...';
                    }
                }, 1000);
            });
        }

        async function obtenerCodigoPasoAPaso(intentosLista, sysPrompt, historialActual, burbuja, prefijoAccion) {
            let keysInvalidasSesion = {};
            let ultimoDetalleError = '';

            for (let ronda = 1; ronda <= 3 && !canceladoPorUsuario; ronda++) {
                let huboSaturacion503 = false;

                for (let i = 0; i < intentosLista.length && !canceladoPorUsuario; i++) {
                    const cand = intentosLista[i];
                    if (keysInvalidasSesion[cand.key]) {
                        continue;
                    }

                    burbuja.textContent = prefijoAccion + ' con ' + cand.modelo_corto + ' (' + cand.prov.toUpperCase() + ' Key #' + cand.num_key + ')...';
                    chatBox.scrollTop = chatBox.scrollHeight;

                    await pausaUI(60);
                    if (canceladoPorUsuario) break;

                    const respRaw = await adsk.fusionSendData('intento_unitario_ia', JSON.stringify({
                        prov: cand.prov,
                        key: cand.key,
                        num_key: cand.num_key,
                        modelo: cand.modelo,
                        sys_prompt: sysPrompt,
                        historial: historialActual
                    }));

                    const resp = JSON.parse(respRaw);

                    if (resp.estado === 'OK') {
                        return {
                            exito: true,
                            codigo: resp.codigo,
                            etiqueta: cand.modelo_corto + ' | ' + cand.prov.toUpperCase() + ' Key #' + cand.num_key
                        };
                    } else if (resp.estado === 'SATURADO') {
                        huboSaturacion503 = true;
                        ultimoDetalleError = '[' + cand.prov.toUpperCase() + ' Key#' + cand.num_key + ' - ' + cand.modelo_corto + ']: ' + resp.detalle;
                        continue;
                    } else if (resp.estado === 'NO_ENCONTRADO') {
                        continue;
                    } else if (resp.estado === 'KEY_SIN_SALDO') {
                        keysInvalidasSesion[cand.key] = true;
                        ultimoDetalleError = '[' + cand.prov.toUpperCase() + ' Key#' + cand.num_key + ']: ' + resp.detalle;
                        continue;
                    } else {
                        ultimoDetalleError = '[' + cand.prov.toUpperCase() + ' Key#' + cand.num_key + ']: ' + resp.detalle;
                        continue;
                    }
                }

                if (huboSaturacion503 && !canceladoPorUsuario && ronda < 3) {
                    await cuentaRegresiva(
                        5 * ronda,
                        'Servidores con alta demanda (' + ultimoDetalleError + '). Ronda ' + ronda + '/3.',
                        burbuja
                    );
                } else {
                    break;
                }
            }

            if (canceladoPorUsuario) {
                return { exito: false, mensaje: '⏹ Operación cancelada por el usuario.' };
            }
            return { exito: false, mensaje: '❌ No se pudo conectar:\n' + (ultimoDetalleError || 'Verifica tus API Keys.') };
        }

        function encolarPedido() {
            const texto = promptInput.value.trim();
            if (!texto) return;

            const prov = providerSelect.value;
            const keyTemporal = keyInput.value.trim();

            agregarMensaje(texto, 'user');
            promptInput.value = '';

            const pos = colaPedidos.length + 1;
            const burbuja = agregarMensaje(
                procesandoCola ? '📋 En cola de espera (Posición #' + pos + ')...' : '⚙️ Analizando diseño 3D...',
                'bot'
            );

            colaPedidos.push({
                prompt: texto,
                proveedor: prov,
                temp_key: keyTemporal,
                burbuja: burbuja
            });

            if (!procesandoCola) {
                procesarSiguienteEnCola();
            }
        }

        async function procesarSiguienteEnCola() {
            if (colaPedidos.length === 0) {
                procesandoCola = false;
                btnCancelar.style.display = 'none';
                return;
            }
            procesandoCola = true;
            canceladoPorUsuario = false;
            btnCancelar.style.display = 'block';

            const item = colaPedidos[0];

            try {
                await pausaUI(40);
                const prepRaw = await adsk.fusionSendData('preparar_pedido', JSON.stringify({
                    prompt: item.prompt,
                    proveedor: item.proveedor,
                    temp_key: item.temp_key
                }));

                consultarEstadoKey();
                const prep = JSON.parse(prepRaw);

                if (!prep.exito) {
                    item.burbuja.className = 'msg error';
                    item.burbuja.textContent = prep.mensaje;
                    colaPedidos.shift();
                    procesarSiguienteEnCola();
                    return;
                }

                let historialTrabajo = prep.historial.slice();
                let exitoFinal = false;
                let errorFinal = '';
                let ultimoResIA = null;

                for (let fix = 1; fix <= 3 && !canceladoPorUsuario; fix++) {
                    const esFix = fix > 1;
                    item.burbuja.className = esFix ? 'msg autofix' : 'msg bot';
                    const prefijo = esFix ? '🔧 Auto-corrigiendo código (' + fix + '/3)' : '⚙️ Generando 3D';

                    const resIA = await obtenerCodigoPasoAPaso(
                        prep.intentos,
                        prep.system_prompt,
                        historialTrabajo,
                        item.burbuja,
                        prefijo
                    );

                    ultimoResIA = resIA;

                    if (!resIA.exito) {
                        errorFinal = resIA.mensaje;
                        break;
                    }

                    item.burbuja.textContent = '🔨 Dibujando pieza en Fusion 360...';
                    await pausaUI(40);

                    const notaFix = esFix ? ' | 🛠️ Auto-Fix #' + fix : '';
                    const execRaw = await adsk.fusionSendData('ejecutar_codigo', JSON.stringify({
                        codigo: resIA.codigo,
                        mensaje_usuario: prep.mensaje_usuario,
                        etiqueta: resIA.etiqueta + notaFix
                    }));

                    const execRes = JSON.parse(execRaw);
                    if (execRes.exito) {
                        item.burbuja.className = 'msg bot';
                        item.burbuja.textContent = '';

                        const txtSpan = document.createElement('span');
                        txtSpan.textContent = execRes.mensaje;
                        item.burbuja.appendChild(txtSpan);

                        inyectarBotonCodigo(item.burbuja, resIA.codigo, item.prompt, false);

                        exitoFinal = true;
                        break;
                    } else {
                        errorFinal = '❌ Error de código en Fusion 360:\n' + execRes.error_corto;
                        if (fix < 3 && !canceladoPorUsuario) {
                            item.burbuja.className = 'msg autofix';
                            item.burbuja.textContent = '⚠️ Ajustando detalle geométrico (' + execRes.error_corto.slice(0, 50) + '...).\n🔧 Auto-corrigiendo con la IA...';
                            historialTrabajo.push({ role: 'assistant', content: resIA.codigo });
                            historialTrabajo.push({
                                role: 'user',
                                content:
                                    'EL CÓDIGO ANTERIOR FALLÓ AL EJECUTARSE EN FUSION 360 (los cambios parciales fueron revertidos).\n' +
                                    'ERROR EXACTO:\n' + execRes.traceback_completo + '\n\n' +
                                    'Corrige el código usando la API de Fusion 360 y devuelve ÚNICAMENTE el código Python completo corregido.'
                            });
                            await pausaUI(500);
                        }
                    }
                }

                if (!exitoFinal) {
                    item.burbuja.className = 'msg error';
                    item.burbuja.textContent = '';

                    const txtSpanError = document.createElement('span');
                    txtSpanError.textContent = errorFinal;
                    item.burbuja.appendChild(txtSpanError);

                    if (ultimoResIA && ultimoResIA.codigo) {
                        inyectarBotonCodigo(item.burbuja, ultimoResIA.codigo, item.prompt, true);
                    }
                }

            } catch (err) {
                item.burbuja.className = 'msg error';
                item.burbuja.textContent = '❌ Error inesperado: ' + String(err);
            }

            colaPedidos.shift();
            chatBox.scrollTop = chatBox.scrollHeight;
            procesarSiguienteEnCola();
        }

        function guardarKey() {
            const k = keyInput.value.trim();
            const prov = providerSelect.value;
            if (!k) {
                agregarMensaje('Pega una o más claves separadas por coma en la caja superior.', 'error');
                return;
            }
            adsk.fusionSendData('guardar_key', JSON.stringify({ proveedor: prov, key: k })).then(res => {
                const datos = JSON.parse(res);
                keyInput.value = '';
                if (!datos.exito) {
                    agregarMensaje(datos.mensaje, 'error');
                } else {
                    keyStatus.textContent = '🔑 ' + datos.resumen;
                    agregarMensaje('✅ ' + datos.mensaje, 'bot');
                }
            });
        }

        function borrarKeys() {
            const prov = providerSelect.value;
            if (prov === 'auto') {
                agregarMensaje('Selecciona un proveedor específico en el menú para borrar sus claves.', 'bot');
                return;
            }
            adsk.fusionSendData('borrar_keys', JSON.stringify({ proveedor: prov })).then(res => {
                consultarEstadoKey();
                agregarMensaje('🗑️ Se borraron las claves de ' + prov.toUpperCase() + '.', 'bot');
            });
        }

        function limpiarChat() {
            adsk.fusionSendData('limpiar_memoria', '{}').then(res => {
                chatBox.innerHTML = '<div class="msg bot">' + HTML_BIENVENIDA + '</div>';
            });
        }

        promptInput.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                encolarPedido();
            }
        });
    </script>
</body>
</html>
"""


# ==============================================================================
# LECTURA DEL DISEÑO, SELECCIÓN CON MOUSE Y REVERSIÓN DE FALLOS PARCIALES
# ==============================================================================
def capturar_selecciones_mouse(ui):
    sel_all = []
    sel_faces = []
    sel_edges = []
    sel_bodies = []
    sel_vertices = []
    descripciones = []

    try:
        selecciones = ui.activeSelections
        for i in range(selecciones.count):
            ent = selecciones.item(i).entity
            sel_all.append(ent)
            tipo = ent.objectType

            if "BRepFace" in tipo:
                face = adsk.fusion.BRepFace.cast(ent)
                sel_faces.append(face)
                pt = face.pointOnFace
                px, py, pz = round(pt.x * 10, 2), round(pt.y * 10, 2), round(pt.z * 10, 2)
                geom_tipo = face.geometry.objectType.split("::")[-1]
                area_mm2 = round(face.area * 100, 1)
                cuerpo_nom = face.body.name if face.body else "Cuerpo"
                descripciones.append(
                    f"sel_faces[{len(sel_faces)-1}]: Cara ({geom_tipo}) en '{cuerpo_nom}', "
                    f"punto ref=({px}, {py}, {pz}), área={area_mm2}"
                )

            elif "BRepEdge" in tipo:
                edge = adsk.fusion.BRepEdge.cast(ent)
                sel_edges.append(edge)
                pt = edge.pointOnEdge
                px, py, pz = round(pt.x * 10, 2), round(pt.y * 10, 2), round(pt.z * 10, 2)
                largo_mm = round(edge.length * 10, 2)
                geom_tipo = edge.geometry.objectType.split("::")[-1]
                descripciones.append(
                    f"sel_edges[{len(sel_edges)-1}]: Arista ({geom_tipo}), "
                    f"largo={largo_mm}, punto ref=({px}, {py}, {pz})"
                )

            elif "BRepBody" in tipo:
                body = adsk.fusion.BRepBody.cast(ent)
                sel_bodies.append(body)
                descripciones.append(f"sel_bodies[{len(sel_bodies)-1}]: Cuerpo '{body.name}'")

            elif "BRepVertex" in tipo or "SketchPoint" in tipo:
                sel_vertices.append(ent)
                pt = ent.geometry
                px, py, pz = round(pt.x * 10, 2), round(pt.y * 10, 2), round(pt.z * 10, 2)
                descripciones.append(f"sel_vertices[{len(sel_vertices)-1}]: Punto/Vértice en ({px}, {py}, {pz})")

            else:
                descripciones.append(f"sel[{i}]: Entidad tipo {tipo.split('::')[-1]}")
    except:
        pass

    texto_seleccion = (
        "ELEMENTOS SELECCIONADOS CON EL MOUSE POR EL USUARIO AHORA MISMO:\n" +
        ("\n".join(f"  * {d}" for d in descripciones) if descripciones else "  * Ninguno (nada seleccionado con el mouse)")
    )

    diccionario_variables = {
        "sel": sel_all,
        "sel_faces": sel_faces,
        "sel_edges": sel_edges,
        "sel_bodies": sel_bodies,
        "sel_vertices": sel_vertices
    }
    return diccionario_variables, texto_seleccion, len(sel_all)


def tomar_snapshot_escena(design):
    root = design.rootComponent
    return {
        "sketches": root.sketches.count,
        "bodies": root.bRepBodies.count,
        "planes": root.constructionPlanes.count
    }


def revertir_cambios_parciales(design, snap):
    root = design.rootComponent
    try:
        while root.bRepBodies.count > snap["bodies"]:
            root.bRepBodies.item(root.bRepBodies.count - 1).deleteMe()
    except:
        pass
    try:
        while root.sketches.count > snap["sketches"]:
            root.sketches.item(root.sketches.count - 1).deleteMe()
    except:
        pass
    try:
        while root.constructionPlanes.count > snap["planes"]:
            root.constructionPlanes.item(root.constructionPlanes.count - 1).deleteMe()
    except:
        pass


def obtener_contexto_diseno(design, texto_seleccion):
    root = design.rootComponent
    cuerpos = []
    for b in root.bRepBodies:
        bb = b.boundingBox
        dx = round((bb.maxPoint.x - bb.minPoint.x) * 10, 2)
        dy = round((bb.maxPoint.y - bb.minPoint.y) * 10, 2)
        dz = round((bb.maxPoint.z - bb.minPoint.z) * 10, 2)
        cuerpos.append(f"'{b.name}' ({dx}x{dy}x{dz})")
    
    bocetos = [s.name for s in root.sketches]
    params = [f"{p.name}={p.expression}" for p in design.userParameters]
    
    return (
        f"ESTADO ACTUAL DEL ARCHIVO EN PANTALLA:\n"
        f"- Cuerpos 3D ({len(cuerpos)}): {', '.join(cuerpos) if cuerpos else 'Ninguno'}\n"
        f"- Bocetos ({len(bocetos)}): {', '.join(bocetos) if bocetos else 'Ninguno'}\n"
        f"- Parámetros: {', '.join(params) if params else 'Ninguno'}\n"
        f"{texto_seleccion}\n"
    )


def obtener_system_prompt():
    return (
        "Eres un asistente experto de diseño mecánico en Autodesk Fusion 360 vía Python API.\n"
        "REGLAS INTERNAS ESTRICTAS:\n"
        "1. CONVERSIÓN DE UNIDADES: En el entorno ya existe la función `u(valor)` (y su alias `mm(valor)`) "
        "que convierte milímetros a los centímetros internos de Fusion (`valor * 0.1`). "
        "Usa SIEMPRE `u(...)` para todas las cotas, radios, coordenadas `Point3D.create(u(x), u(y), u(z))` y distancias de extrusión.\n"
        "2. VARIABLES YA DEFINIDAS: `adsk`, `app`, `ui`, `design`, `rootComp`, `u`, `mm`, `math`, `sel`, `sel_faces`, `sel_edges`, `sel_bodies`, `sel_vertices`. "
        "NO definas `def run(context):` ni cambies `defaultLengthUnits`.\n"
        "3. INTERPRETACIÓN GEOMÉTRICA DIRECTA:\n"
        "   - Si el usuario pide un cubo con 1 o 2 dimensiones (ej. 'cubo de 4' o 'cubo 4x4'), asume que mide 4x4x4 (ancho=4, profundo=4, alto=4).\n"
        "   - Si pide un paralelepípedo o caja (ej. 'altura 4, ancho 5, largo 6'), crea el rectángulo en XY de `u(ancho)` por `u(largo)` y extrúyelo `u(altura)`.\n"
        "   - Ejemplo exacto para crear un cubo o paralelepípedo en XY:\n"
        "     `sk = rootComp.sketches.add(rootComp.xYConstructionPlane)`\n"
        "     `sk.sketchCurves.sketchLines.addTwoPointRectangle(adsk.core.Point3D.create(0, 0, 0), adsk.core.Point3D.create(u(ancho), u(largo), 0))`\n"
        "     `prof = sk.profiles.item(0)`\n"
        "     `rootComp.features.extrudeFeatures.addSimple(prof, adsk.core.ValueInput.createByReal(u(altura)), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)`\n"
        "4. REGLAS ANTI-ERRORES DE LA API DE FUSION 360:\n"
        "   - En un `Sketch` 2D, todos los puntos dibujados (`addByCenterRadius`, `addTwoPointRectangle`, `addByTwoPoints`) deben llevar Z=0: `adsk.core.Point3D.create(u(x), u(y), 0)`.\n"
        "   - Para empalmes (fillets): `edgeCol = adsk.core.ObjectCollection.create()`, añade aristas con `edgeCol.add(e)`, luego `fillets = rootComp.features.filletFeatures`, `fInput = fillets.createInput()`, `fInput.addConstantRadiusEdgeSet(edgeCol, adsk.core.ValueInput.createByReal(u(radio)), True)`, `fillets.add(fInput)`.\n"
        "   - Para chaflanes (chamfers): `chamfers = rootComp.features.chamferFeatures`, `cInput = chamfers.createInput2()`, `cInput.chamferEdgeSets.addEqualDistanceChamferEdgeSet(edgeCol, adsk.core.ValueInput.createByReal(u(dist)), False)`, `chamfers.add(cInput)`.\n"
        "5. SALIDA: Devuelve ÚNICAMENTE código Python puro ejecutable, sin bloques ```python ni texto explicativo."
    )


def limpiar_bloques_codigo(texto_codigo):
    texto_codigo = re.sub(r"<think>[\s\S]*?</think>", "", texto_codigo, flags=re.IGNORECASE).strip()
    match_bloque = re.search(r"```(?:python)?\s*([\s\S]*?)```", texto_codigo, re.IGNORECASE)
    if match_bloque:
        texto_codigo = match_bloque.group(1).strip()
    else:
        texto_codigo = re.sub(r"^```python\s*", "", texto_codigo.strip(), flags=re.IGNORECASE)
        texto_codigo = re.sub(r"^```\s*", "", texto_codigo.strip())
        texto_codigo = re.sub(r"\s*```$", "", texto_codigo.strip())
    return texto_codigo.strip()


# ==============================================================================
# CONECTOR HTTP ATÓMICO (1 SOLA LLAMADA RÁPIDA DE <= 12 SEGUNDOS POR TURNO)
# ==============================================================================
def ejecutar_intento_unico_http(prov, clean_key, modelo, sys_prompt, ultimos):
    ctx_ssl = ssl.create_default_context()
    ua_header = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Fusion360CAD/8.2"
    conn = None

    try:
        if prov == "gemini":
            host = "generativelanguage.googleapis.com"
            contents_gemini = [
                {"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]}
                for m in ultimos
            ]
            payload = {
                "system_instruction": {"parts": [{"text": sys_prompt}]},
                "contents": contents_gemini,
                "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048}
            }
            datos = json.dumps(payload).encode("utf-8")
            conn = http.client.HTTPSConnection(host, timeout=14, context=ctx_ssl)
            cabeceras = {
                "Content-Type": "application/json",
                "x-goog-api-key": clean_key,
                "Connection": "close",
                "User-Agent": ua_header
            }
            conn.request("POST", f"/v1beta/models/{modelo}:generateContent", body=datos, headers=cabeceras)
            res = conn.getresponse()
            cuerpo = res.read().decode("utf-8")

            if res.status == 200:
                resultado = json.loads(cuerpo)
                candidatos = resultado.get("candidates", [])
                if candidatos:
                    partes = candidatos[0].get("content", {}).get("parts", [])
                    if partes and "text" in partes[0]:
                        return {"estado": "OK", "codigo": limpiar_bloques_codigo(partes[0]["text"])}

            if res.status in (408, 429, 500, 502, 503, 504):
                return {"estado": "SATURADO", "detalle": f"HTTP {res.status}"}
            if res.status == 404:
                return {"estado": "NO_ENCONTRADO", "detalle": "HTTP 404"}
            if res.status in (400, 401, 403):
                return {"estado": "KEY_SIN_SALDO", "detalle": f"HTTP {res.status} (Key inválida o sin permiso)"}
            return {"estado": "ERROR", "detalle": f"HTTP {res.status}: {cuerpo[:120]}"}

        elif prov in ("groq", "openrouter", "openai", "deepseek"):
            if prov == "groq":
                host, ruta = "api.groq.com", "/openai/v1/chat/completions"
            elif prov == "openrouter":
                host, ruta = "openrouter.ai", "/api/v1/chat/completions"
            elif prov == "openai":
                host, ruta = "api.openai.com", "/v1/chat/completions"
            else:
                host, ruta = "api.deepseek.com", "/v1/chat/completions"

            mensajes_api = [{"role": "system", "content": sys_prompt}] + ultimos
            payload = {"model": modelo, "messages": mensajes_api, "temperature": 0.1}
            datos = json.dumps(payload).encode("utf-8")

            conn = http.client.HTTPSConnection(host, timeout=14, context=ctx_ssl)
            cabeceras = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {clean_key}",
                "Connection": "close",
                "User-Agent": ua_header
            }
            if prov == "openrouter":
                cabeceras["HTTP-Referer"] = "https://www.autodesk.com"
                cabeceras["X-Title"] = "Fusion360 Multi-IA CAD"

            conn.request("POST", ruta, body=datos, headers=cabeceras)
            res = conn.getresponse()
            cuerpo = res.read().decode("utf-8")

            if res.status == 200:
                resultado = json.loads(cuerpo)
                if "error" in resultado:
                    return {"estado": "SATURADO", "detalle": "Modelo ocupado"}
                choices = resultado.get("choices", [])
                if choices and choices[0].get("message", {}).get("content"):
                    return {"estado": "OK", "codigo": limpiar_bloques_codigo(choices[0]["message"]["content"])}

            if res.status in (408, 429, 500, 502, 503, 504):
                return {"estado": "SATURADO", "detalle": f"HTTP {res.status}"}
            if res.status == 404:
                return {"estado": "NO_ENCONTRADO", "detalle": "HTTP 404"}
            if res.status in (401, 402, 403):
                keys_sin_saldo_o_invalidas.add(clean_key)
                return {"estado": "KEY_SIN_SALDO", "detalle": f"HTTP {res.status} (Sin saldo o Key inválida)"}
            return {"estado": "ERROR", "detalle": f"HTTP {res.status}: {cuerpo[:120]}"}

        elif prov == "claude":
            host = "api.anthropic.com"
            payload = {
                "model": modelo,
                "max_tokens": 2048,
                "system": sys_prompt,
                "messages": ultimos,
                "temperature": 0.1
            }
            datos = json.dumps(payload).encode("utf-8")
            conn = http.client.HTTPSConnection(host, timeout=15, context=ctx_ssl)
            cabeceras = {
                "Content-Type": "application/json",
                "x-api-key": clean_key,
                "anthropic-version": "2023-06-01",
                "Connection": "close",
                "User-Agent": ua_header
            }
            conn.request("POST", "/v1/messages", body=datos, headers=cabeceras)
            res = conn.getresponse()
            cuerpo = res.read().decode("utf-8")

            if res.status == 200:
                resultado = json.loads(cuerpo)
                return {"estado": "OK", "codigo": limpiar_bloques_codigo(resultado["content"][0]["text"])}
            if res.status in (408, 429, 500, 502, 503, 504, 529):
                return {"estado": "SATURADO", "detalle": f"HTTP {res.status}"}
            if res.status in (401, 402, 403):
                keys_sin_saldo_o_invalidas.add(clean_key)
                return {"estado": "KEY_SIN_SALDO", "detalle": f"HTTP {res.status}"}
            return {"estado": "ERROR", "detalle": f"HTTP {res.status}: {cuerpo[:120]}"}

    except Exception as e:
        return {"estado": "SATURADO", "detalle": f"Timeout/Red ({str(e)[:45]})"}
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass


def construir_lista_intentos(prov_elegido, pool):
    global indice_rotacion
    modelos_por_prov = {
        "gemini": MODELOS_GEMINI_BASE,
        "groq": MODELOS_GROQ,
        "openrouter": MODELOS_OPENROUTER,
        "claude": ["claude-3-7-sonnet-latest"],
        "openai": ["gpt-4o"],
        "deepseek": ["deepseek-chat"]
    }

    if pool.get("gemini"):
        modelos_por_prov["gemini"] = obtener_modelos_gemini_activos(pool["gemini"][0])

    intentos = []

    def agregar_candidatos_de_proveedor(p):
        claves_p = [
            (idx + 1, k) for idx, k in enumerate(pool.get(p, []))
            if k not in keys_sin_saldo_o_invalidas
        ]
        if not claves_p:
            return
        turno = indice_rotacion.get(p, 0) % len(claves_p)
        indice_rotacion[p] = turno + 1
        claves_rotadas = claves_p[turno:] + claves_p[:turno]

        for mod in modelos_por_prov.get(p, []):
            mod_corto = mod.split("/")[-1].replace(":free", "")[:22]
            for num_k, k_str in claves_rotadas:
                intentos.append({
                    "prov": p,
                    "key": k_str,
                    "num_key": num_k,
                    "modelo": mod,
                    "modelo_corto": mod_corto
                })

    if prov_elegido == "auto":
        for p in ("gemini", "groq", "openrouter", "claude", "openai", "deepseek"):
            agregar_candidatos_de_proveedor(p)
    else:
        agregar_candidatos_de_proveedor(prov_elegido)
        for p_respaldo in ("groq", "gemini", "openrouter"):
            if p_respaldo != prov_elegido:
                agregar_candidatos_de_proveedor(p_respaldo)

    return intentos


# ==============================================================================
# PUENTE ENTRE EL CHAT HTML Y FUSION 360
# ==============================================================================
class PaletteIncomingHandler(adsk.core.HTMLEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        global historial_chat, indice_rotacion
        html_args = adsk.core.HTMLEventArgs.cast(args)

        if html_args.action == "estado_key":
            datos = json.loads(html_args.data)
            prov = datos.get("proveedor", "gemini")
            html_args.returnData = resumir_pool_proveedor(prov)
            return

        if html_args.action == "listar_macros":
            html_args.returnData = json.dumps(cargar_biblioteca_macros(), ensure_ascii=False)
            return

        if html_args.action == "guardar_macro":
            datos = json.loads(html_args.data)
            nuevas = agregar_o_actualizar_macro(
                datos.get("nombre", "Pieza"),
                datos.get("prompt", ""),
                datos.get("codigo", "")
            )
            html_args.returnData = json.dumps({"exito": True, "macros": nuevas}, ensure_ascii=False)
            return

        if html_args.action == "borrar_macro":
            datos = json.loads(html_args.data)
            nuevas = borrar_macro_por_nombre(datos.get("nombre", ""))
            html_args.returnData = json.dumps({"exito": True, "macros": nuevas}, ensure_ascii=False)
            return

        if html_args.action == "guardar_key":
            datos = json.loads(html_args.data)
            prov = datos.get("proveedor", "gemini")
            raw_k = datos.get("key", "").strip()

            if raw_k.startswith("gen-lang-client"):
                html_args.returnData = json.dumps({
                    "exito": False,
                    "mensaje": "⚠️ 'gen-lang-client...' es el ID del Proyecto, no una API Key."
                })
                return

            lista_actual, nuevas, prov_final = agregar_keys_proveedor(prov, raw_k)
            resumen = resumir_pool_proveedor(prov)
            html_args.returnData = json.dumps({
                "exito": True,
                "resumen": resumen,
                "mensaje": f"Se añadió {nuevas} Key(s) al pool de {prov_final.upper()} (Total en {prov_final.upper()}: {len(lista_actual)})."
            })
            return

        if html_args.action == "borrar_keys":
            datos = json.loads(html_args.data)
            prov = datos.get("proveedor", "gemini")
            borrar_keys_proveedor(prov)
            html_args.returnData = "OK"
            return

        if html_args.action == "limpiar_memoria":
            historial_chat.clear()
            html_args.returnData = "OK"
            return

        if html_args.action == "preparar_pedido":
            try:
                app = adsk.core.Application.get()
                ui = app.userInterface
                design = adsk.fusion.Design.cast(app.activeProduct)

                if not design:
                    html_args.returnData = json.dumps({"exito": False, "mensaje": "Abre un documento 3D activo primero."})
                    return

                datos = json.loads(html_args.data)
                prompt = datos.get("prompt", "")
                prov = datos.get("proveedor", "gemini")
                temp_key = datos.get("temp_key", "").strip()

                if temp_key and not temp_key.startswith("gen-lang-client"):
                    agregar_keys_proveedor(prov, temp_key)

                pool = cargar_pool_keys()
                intentos = construir_lista_intentos(prov, pool)

                if not intentos:
                    html_args.returnData = json.dumps({
                        "exito": False,
                        "mensaje": f"⚠️ No hay ninguna API Key válida en {prov.upper()}. Pega tu clave arriba y dale a '+ Key'."
                    })
                    return

                _, texto_seleccion, _ = capturar_selecciones_mouse(ui)
                contexto_3d = obtener_contexto_diseno(design, texto_seleccion)
                mensaje_usuario = f"{contexto_3d}\nINSTRUCCIÓN DEL USUARIO: {prompt}"
                ultimos = (historial_chat + [{"role": "user", "content": mensaje_usuario}])[-10:]

                html_args.returnData = json.dumps({
                    "exito": True,
                    "intentos": intentos,
                    "system_prompt": obtener_system_prompt(),
                    "historial": ultimos,
                    "mensaje_usuario": mensaje_usuario
                })
            except Exception as e:
                html_args.returnData = json.dumps({"exito": False, "mensaje": f"❌ Error al preparar escena: {str(e)}"})
            return

        if html_args.action == "intento_unitario_ia":
            datos = json.loads(html_args.data)
            resultado = ejecutar_intento_unico_http(
                datos.get("prov", "gemini"),
                datos.get("key", ""),
                datos.get("modelo", "gemini-flash-latest"),
                datos.get("sys_prompt", ""),
                datos.get("historial", [])
            )
            html_args.returnData = json.dumps(resultado)
            return

        if html_args.action == "ejecutar_codigo":
            app = adsk.core.Application.get()
            ui = app.userInterface
            design = adsk.fusion.Design.cast(app.activeProduct)
            snap = tomar_snapshot_escena(design)

            try:
                datos = json.loads(html_args.data)
                codigo = datos.get("codigo", "")
                mensaje_usuario = datos.get("mensaje_usuario", "")
                etiqueta = datos.get("etiqueta", "")

                vars_seleccion, _, total_sel = capturar_selecciones_mouse(ui)
                conversor = lambda valor: float(valor) * 0.1

                entorno = {
                    "adsk": adsk,
                    "app": app,
                    "ui": ui,
                    "design": design,
                    "rootComp": design.rootComponent,
                    "u": conversor,
                    "mm": conversor,
                    "math": math,
                    "__builtins__": __builtins__
                }
                entorno.update(vars_seleccion)

                exec(codigo, entorno)

                historial_chat.append({"role": "user", "content": mensaje_usuario})
                historial_chat.append({"role": "assistant", "content": codigo})

                total_cuerpos = design.rootComponent.bRepBodies.count
                info_sel = f" | Selección: {total_sel}" if total_sel > 0 else ""
                html_args.returnData = json.dumps({
                    "exito": True,
                    "mensaje": f"✅ Operación completada ({etiqueta}{info_sel}).\nCuerpos en diseño: {total_cuerpos}"
                })
            except Exception as e:
                revertir_cambios_parciales(design, snap)
                tb_completo = traceback.format_exc()
                html_args.returnData = json.dumps({
                    "exito": False,
                    "error_corto": str(e),
                    "traceback_completo": f"{tb_completo}\nCÓDIGO QUE FALLÓ:\n{codigo}"
                })
            return


def limpiar_paneles_antiguos(ui):
    ids_viejos = (
        "GeminiChatPalette_ID",
        "MultiIAChatPalette_ID",
        "MultiIAChatPalette_V4_ID",
        "MultiIAChatPalette_V5_ID",
        "MultiIAChatPalette_V6_ID",
        "MultiIAChatPalette_V7_ID",
        "MultiIAChatPalette_V8_ID",
        "MultiIAChatPalette_V81_ID",
        PALETTE_ID
    )
    for pid in ids_viejos:
        old_p = ui.palettes.itemById(pid)
        if old_p:
            try:
                old_p.deleteMe()
            except:
                pass


def crear_panel_inicial(ui):
    limpiar_paneles_antiguos(ui)
    cargar_pool_keys()
    cargar_biblioteca_macros()

    ruta_html = os.path.join(CARPETA_SCRIPT, "chat_ui_v82.html")
    with open(ruta_html, "w", encoding="utf-8") as f:
        f.write(HTML_CONTENT)

    url_local = ruta_html.replace("\\", "/")

    palette = ui.palettes.add(
        PALETTE_ID,
        "Chat Multi-IA CAD v8.2",
        url_local,
        True,
        True,
        True,
        385,
        610,
        True
    )

    palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
    palette.isVisible = True

    onHTMLEvent = PaletteIncomingHandler()
    palette.incomingFromHTML.add(onHTMLEvent)
    handlers.append(onHTMLEvent)
    return palette


def alternar_panel_desde_boton(ui):
    palette = ui.palettes.itemById(PALETTE_ID)
    if not palette:
        crear_panel_inicial(ui)
    else:
        palette.isVisible = not palette.isVisible


class ShowPaletteCommandExecuteHandler(adsk.core.CommandEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        app = adsk.core.Application.get()
        alternar_panel_desde_boton(app.userInterface)


class ShowPaletteCommandCreatedHandler(adsk.core.CommandCreatedEventHandler):
    def __init__(self):
        super().__init__()
    def notify(self, args):
        onExecute = ShowPaletteCommandExecuteHandler()
        args.command.execute.add(onExecute)
        handlers.append(onExecute)


def run(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        adsk.autoTerminate(False)

        carpeta_res = asegurar_iconos_barra()

        ids_comandos = (
            "GeminiPaletteBtn_ID",
            "GeminiPaletteBtn_V4_ID",
            "GeminiPaletteBtn_V5_ID",
            "GeminiPaletteBtn_V6_ID",
            "GeminiPaletteBtn_V7_ID",
            "GeminiPaletteBtn_V8_ID",
            "GeminiPaletteBtn_V81_ID",
            CMD_ID
        )
        for cid in ids_comandos:
            old_cmd = ui.commandDefinitions.itemById(cid)
            if old_cmd:
                try:
                    old_cmd.deleteMe()
                except:
                    pass

        cmdDef = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_DESC, carpeta_res)
        onCreated = ShowPaletteCommandCreatedHandler()
        cmdDef.commandCreated.add(onCreated)
        handlers.append(onCreated)

        for panel_id in ("SolidCreatePanel", "SolidScriptsAddinsPanel"):
            panel = ui.allToolbarPanels.itemById(panel_id)
            if panel:
                for cid in ids_comandos:
                    cntrl = panel.controls.itemById(cid)
                    if cntrl:
                        cntrl.deleteMe()
                btn = panel.controls.addCommand(cmdDef)
                btn.isPromoted = True
                btn.isPromotedByDefault = True

        qat = ui.toolbars.itemById("QAT")
        if qat:
            for cid in ids_comandos:
                cntrl_qat = qat.controls.itemById(cid)
                if cntrl_qat:
                    cntrl_qat.deleteMe()
            qat.controls.addCommand(cmdDef)

        crear_panel_inicial(ui)

    except:
        if ui:
            ui.messageBox(f"Error al iniciar el panel:\n{traceback.format_exc()}")


def stop(context):
    ui = None
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface

        limpiar_paneles_antiguos(ui)
        ids_comandos = (
            "GeminiPaletteBtn_ID",
            "GeminiPaletteBtn_V4_ID",
            "GeminiPaletteBtn_V5_ID",
            "GeminiPaletteBtn_V6_ID",
            "GeminiPaletteBtn_V7_ID",
            "GeminiPaletteBtn_V8_ID",
            "GeminiPaletteBtn_V81_ID",
            CMD_ID
        )

        for panel_id in ("SolidCreatePanel", "SolidScriptsAddinsPanel"):
            panel = ui.allToolbarPanels.itemById(panel_id)
            if panel:
                for cid in ids_comandos:
                    cntrl = panel.controls.itemById(cid)
                    if cntrl:
                        cntrl.deleteMe()

        qat = ui.toolbars.itemById("QAT")
        if qat:
            for cid in ids_comandos:
                cntrl_qat = qat.controls.itemById(cid)
                if cntrl_qat:
                    cntrl_qat.deleteMe()

        for cid in ids_comandos:
            cmdDef = ui.commandDefinitions.itemById(cid)
            if cmdDef:
                cmdDef.deleteMe()
    except:
        if ui:
            ui.messageBox(f"Error al cerrar el panel:\n{traceback.format_exc()}")