# Chat Multi-IA CAD para Fusion 360 🚀

Este es un complemento (Add-In) para Autodesk Fusion360 que integra Inteligencia Artificial (Google Gemini, Groq, OpenRouter, Claude, OpenAI y DeepSeek) directamente en la interfaz del programa. Permite modelar, modificar y automatizar geometría 3D utilizando prompts con un chatBox.

## ✨ Características Principales

* ⚡ **Conexión Asíncrona:** Ejecución en segundo plano sin congelar Fusion 360.
* 🔄 **Pool de Tokens Anti-Saturación:** Soporta múltiples API Keys con rotación automática (Round-Robin) y auto-descubrimiento de modelos rápidos como `gemini-flash-lite-latest`.
* 🖱️ **Selección por Mouse:** Detecta automáticamente caras, aristas, vértices y cuerpos seleccionados en el entorno 3D para aplicar modificaciones locales (ej. empalmes, chaflanes, vaciados).
* 🛠️ **Bucle de Auto-Corrección:** Si el código Python generado por la IA falla al compilar, el script deshace los cambios parciales y le reenvía el error exacto a la IA para que se repare sola.

## ⚙️ Instalación

1. Descarga este repositorio como archivo `.zip` y extráelo, o clónalo con Git.
2. Copia la carpeta completa dentro del directorio de complementos de Fusion 360:
   * **Windows:** `%appdata%\Autodesk\Autodesk Fusion 360\API\AddIns`
   * **Mac:** `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns`
3. Abre Fusion 360 y presiona `Shift + S` para abrir la ventana de *Scripts y complementos*.
4. Ve a la pestaña **Complementos (Add-Ins)**, selecciona `GeminiChatAddIn` y haz clic en **Ejecutar**.
   > *Tip: Marca la casilla "Ejecutar al iniciar" para que se cargue automáticamente cada vez que abras Fusion360.*

## 🔒 Privacidad y Seguridad (API Keys)

Las claves API se guardan de forma **local** en tu computadora (`api_keys.json`) y **nunca** se sincronizan ni se suben a este repositorio.
