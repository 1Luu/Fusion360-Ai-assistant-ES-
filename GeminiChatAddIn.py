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

# Caché en memoria de los modelos reales activos descubiertos en Google AI Studio
modelos_gemini_descubiertos = []
keys_sin_saldo_o_invalidas = set()

CMD_ID = "GeminiPaletteBtn_V8_ID"
CMD_NAME = "Asistente Multi-IA CAD"
CMD_DESC = "Muestra u oculta el panel lateral de modelado asistido por IA (v8.0 Definitivo)"
PALETTE_ID = "MultiIAChatPalette_V8_ID"

CARPETA_USUARIO = os.path.expanduser("~")
RUTA_PERMANENTE_JSON = os.path.join(CARPETA_USUARIO, ".fusion360_multi_ia_keys.json")
CARPETA_SCRIPT = os.path.dirname(os.path.abspath(__file__))
RUTA_LOCAL_JSON = os.path.join(CARPETA_SCRIPT, "api_keys.json")
CARPETA_ICONOS = os.path.join(CARPETA_SCRIPT, "resources", "icono_ia")

# Modelos base por defecto (se complementan automáticamente consultando /v1beta/models)
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


# ==============================================================================
# GENERADOR AUTOMÁTICO DE ICONOS PARA LA BARRA DE FUSION 360
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
# GESTIÓN PERMANENTE DE MÚLTIPLES TOKENS (POOL DE API KEYS)
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
# AUTO-DESCUBRIMIENTO DE MODELOS ACTIVOS EN GOOGLE AI STUDIO (/v1beta/models)
# ==============================================================================
def obtener_modelos_gemini_activos(api_key):
    """
    Consulta directamente a Google qué modelos Flash y Lite están activos hoy
    para esta API Key, evitando errores 404 y teniendo alternativas inmediatas ante errores 503.
    """
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
            "User-Agent": "Fusion360CAD/8.0"
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
# INTERFAZ GRÁFICA HTML/CSS/JS (v8.0 Definitivo)
# ==============================================================================
HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
    * { box-sizing: border-box; font-family: 'Segoe UI', Tahoma, sans-serif; }
    body { margin: 0; padding: 10px; background: #22252a; color: #e8eaed; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
    .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 6px; border-bottom: 1px solid #3c4043; font-size: 12px; color: #9aa0a6; gap: 5px; }
    .badge-v8 { background: #0d47a1; color: #bbdefb; border: 1px solid #2196f3; padding: 2px 6px; border-radius: 4px; font-size: 10px; font-weight: 700; white-space: nowrap; }
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
    .status-bar { font-size: 10.5px; color: #8ab4f8; padding-bottom: 6px; border-bottom: 1px solid #3c4043; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
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
</style>
</head>
<body>
    <div class="header">
        <span class="badge-v8">v8.0 Definitivo</span>
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
        <button class="btn-save" onclick="guardarKey()">+ Añadir Key</button>
        <button class="btn-del-key" title="Borrar las Keys de este proveedor" onclick="borrarKeys()">✕</button>
    </div>
    <div class="status-bar" id="key-status">Cargando tokens guardados...</div>

    <div id="chat-box">
        <div class="msg bot" id="welcome-msg"><span class="welcome-title">👋 Cómo usar el Asistente CAD:</span>1. Elige tu IA arriba y añade tus API Keys (quedan guardadas en tu PC).
2. <b>Opcional:</b> Selecciona con el mouse caras, aristas o cuerpos en tu pieza para modificarlos directamente.
3. Escribe tu instrucción abajo y presiona <b>Enter</b>.<div class="divider"></div><span class="updates-title">🚀 Correcciones clave en v8.0 Definitivo:</span><ul class="updates-list"><li><b>Conexión Unitaria Directa (Sin Bloqueo GIL):</b> Eliminado el bloqueo de hilos de <i>v7.0</i>; cada modelo se consulta en pasos atómicos rápidos.</li><li><b>Salto Anti-503 (`flash-lite` + Auto-Detección):</b> Si <i>gemini-flash-latest</i> da error 503, salta al instante a <i>gemini-flash-lite-latest</i> y a tus Keys de respaldo.</li><li><b>Auto-Corrección + Selección por Mouse:</b> Repara errores geométricos automáticamente.</li></ul></div>
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
        const btnCancelar = document.getElementById('btn-cancelar');
        const HTML_BIENVENIDA = document.getElementById('welcome-msg').innerHTML;

        let colaPedidos = [];
        let procesandoCola = false;
        let canceladoPorUsuario = false;

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
                    keyStatus.textContent = '⚠️ Sin Keys en ' + prov.toUpperCase() + '. Pega una arriba y dale a + Añadir Key.';
                }
            });
        }

        setTimeout(consultarEstadoKey, 300);

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

            // Hasta 3 rondas si todos los servidores están temporalmente con error 503
            for (let ronda = 1; ronda <= 3 && !canceladoPorUsuario; ronda++) {
                let huboSaturacion503 = false;

                for (let i = 0; i < intentosLista.length && !canceladoPorUsuario; i++) {
                    const cand = intentosLista[i];
                    if (keysInvalidasSesion[cand.key]) {
                        continue;
                    }

                    burbuja.textContent = prefijoAccion + ' con ' + cand.modelo_corto + ' (' + cand.prov.toUpperCase() + ' Key #' + cand.num_key + ')...';
                    chatBox.scrollTop = chatBox.scrollHeight;

                    // Pausa de 60ms para que Fusion 360 refresque la ventana y nunca se congele
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

                // Hasta 3 intentos de Auto-Corrección si el código Python generado falla al dibujarse
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
                        item.burbuja.textContent = execRes.mensaje;
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
                    item.burbuja.textContent = errorFinal;
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
    ua_header = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Fusion360CAD/8.0"
    conn = None

    try:
        # 1. GOOGLE GEMINI
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

        # 2. GROQ / OPENROUTER / OPENAI / DEEPSEEK
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

        # 3. CLAUDE
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
    """
    Construye la secuencia inteligente de intentos (Key + Modelo):
    1. Intercala los modelos activos del proveedor elegido con las Keys disponibles
       (para que si 'gemini-flash-latest' tiene caída 503, el segundo intento sea inmediatamente 'gemini-flash-lite-latest').
    2. Añade al final las Keys gratuitas de respaldo (Groq / OpenRouter / Gemini) como salvavidas si el proveedor principal está caído.
    """
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
        # Rotar cuál Key empieza primero en este turno
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
        # Salvavidas automático: si el proveedor elegido cae en 503 total, usa Groq, Gemini u OpenRouter si hay Keys guardadas
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
        global historial_chat
        html_args = adsk.core.HTMLEventArgs.cast(args)

        if html_args.action == "estado_key":
            datos = json.loads(html_args.data)
            prov = datos.get("proveedor", "gemini")
            html_args.returnData = resumir_pool_proveedor(prov)
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

        # PASO 1: Armar contexto y lista de candidatos (Instantáneo)
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
                        "mensaje": f"⚠️ No hay ninguna API Key válida en {prov.upper()}. Pega tu clave arriba y dale a '+ Añadir Key'."
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

        # PASO 2: Ejecutar 1 solo intento HTTP rápido (sin bucles ni bloqueos)
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

        # PASO 3: Dibujar el código 3D en Fusion 360 (con reversión si hay error)
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

    ruta_html = os.path.join(CARPETA_SCRIPT, "chat_ui_v8.html")
    with open(ruta_html, "w", encoding="utf-8") as f:
        f.write(HTML_CONTENT)

    url_local = ruta_html.replace("\\", "/")

    palette = ui.palettes.add(
        PALETTE_ID,
        "Chat Multi-IA CAD v8.0",
        url_local,
        True,
        True,
        True,
        380,
        590,
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