import os
import string
import random
import base64
from datetime import datetime

import math
import shutil
if shutil.which("ffmpeg") is None:
    print("[ERROR] ffmpeg no está disponible. No podrás convertir audio.")
import subprocess

def transcribir_audio_por_segmentos(client, ruta_video, duracion_segmento=30):
    # Convertimos a wav usando ffmpeg
    ruta_audio_wav = ruta_video.replace(".webm", ".wav")
    
    try:
        subprocess.run(
            ["ffmpeg", "-i", ruta_video, "-ar", "16000", "-ac", "1", ruta_audio_wav],
            check=True
        )
    except Exception as e:
        print("[ERROR] Falló la conversión a WAV:", e)
        return "Error al convertir video."

    with open(ruta_audio_wav, "rb") as audio_file:
        transcripcion_completa = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="json"
        )

    texto_completo = transcripcion_completa.text
    palabras = texto_completo.split()
    total_palabras = len(palabras)

    # Estimación de palabras por segmento (usando getattr por si no trae duración)
    duration_estimada = getattr(transcripcion_completa, "duration", len(palabras)/2)

    palabras_por_segmento = max(1, math.ceil(
        total_palabras / (duration_estimada / duracion_segmento)
    ))

    segmentos = []
    for i in range(0, total_palabras, palabras_por_segmento):
        segmento_texto = " ".join(palabras[i:i + palabras_por_segmento])
        segmentos.append(segmento_texto)

    return "\n\n".join(segmentos)



def generar_informe_chatgpt(client, puesto, transcripcion):
    prompt = f"""
Eres un experto en recursos humanos. A partir de la siguiente transcripción de entrevista laboral para la posición "{puesto}", realiza un informe estructurado siguiendo el formato indicado a continuación:

Transcripción:
"{transcripcion}"


Formato del Informe:

Sección 1 - COMPETENCIAS TÉCNICAS

Dominio Técnico:
(Evalúa el conocimiento técnico específico requerido para el puesto, incluyendo certificaciones relevantes y experiencia demostrada). (Si la transcripción tiene datos insuficientes pon "-")

Nivel de Especialización:
(Analiza y comenta el grado de especialización que presenta el candidato en los temas críticos necesarios para desempeñar eficazmente la posición).(Si la transcripción tiene datos insuficientes pon "-")

Sección 2 - SOFT SKILLS

Comunicación:
(Evalúa la capacidad del candidato para expresarse de forma clara, precisa y efectiva durante la entrevista).(Si la transcripción tiene datos insuficientes pon "-")

Trabajo en Equipo:
(Describe la actitud percibida del candidato hacia el trabajo colaborativo, integración grupal y liderazgo interpersonal).(Si la transcripción tiene datos insuficientes pon "-")

Adaptabilidad y Flexibilidad:
(Valora la habilidad del candidato para adaptarse a cambios o trabajar en entornos laborales dinámicos y desafiantes).(Si la transcripción tiene datos insuficientes pon "-")

Solución de Problemas:
(Evalúa su capacidad para identificar problemas y proponer soluciones prácticas y efectivas durante la entrevista).(Si la transcripción tiene datos insuficientes pon "-")

Orientación a Resultados:
(Observa el grado de enfoque hacia objetivos concretos, metas y logro de resultados específicos que demuestra el candidato).(Si la transcripción tiene datos insuficientes pon "-")

Sección 3 - EVALUACIÓN GENERAL

PUNTOS FUERTES DEL CANDIDATO:
(Resalta claramente los aspectos positivos más relevantes detectados durante la entrevista).(Si la transcripción tiene datos insuficientes pon "-")

ÁREAS DE OPORTUNIDAD:
(Indica claramente aquellas áreas o aspectos donde el candidato podría mejorar o requerir desarrollo adicional).(Si la transcripción tiene datos insuficientes pon "-")

OBSERVACIONES ADICIONALES:
(Incluye comentarios relevantes adicionales sobre comportamiento, actitud general, puntualidad, o cualquier otro aspecto que consideres importante destacar).(Si la transcripción tiene datos insuficientes pon "-")
Si algún punto no tienes información solo pon "-"
    """

    respuesta = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=500,
        temperature=0.5
    )

    return respuesta.choices[0].message.content.strip()



from flask import Flask, request, render_template_string, redirect, url_for, session, send_from_directory
from werkzeug.utils import secure_filename

import psycopg2
import psycopg2.extras
from cryptography.fernet import Fernet
from openai import OpenAI

ENCRYPTION_KEY = b'yMybaWCe4meeb3v4LWNI4Sxz7oS54Gn0Fo9yJovqVN0='

def decrypt_api_key(encrypted_data: bytes) -> str:
    f = Fernet(ENCRYPTION_KEY)
    decrypted_bytes = f.decrypt(encrypted_data)
    return decrypted_bytes.decode("utf-8")

def load_api_key_from_file(file_path: str) -> str:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"No se encontró el archivo: {file_path}")
    with open(file_path, "rb") as f:
        encrypted_data = f.read()
    return decrypt_api_key(encrypted_data)



# Cargar la clave descifrada
try:
    HARDCODED_API_KEY = load_api_key_from_file("api.txt")
except Exception as e:
    HARDCODED_API_KEY = ""
    print("[ERROR]", e)

# Inicializar cliente OpenAI con el nuevo SDK (versión >=1.0.0)
client = OpenAI(api_key=HARDCODED_API_KEY)

import math

app = Flask(__name__)
app.secret_key = 'CLAVE_SECRETA_PARA_SESIONES'

# -----------------------------------------------------------------------------------
# CONFIGURACIÓN DE CONEXIÓN A LA BASE DE DATOS
# -----------------------------------------------------------------------------------
DB_HOST = "dpg-cvmsqhumcj7s73c01i3g-a.oregon-postgres.render.com"
DB_PORT = 5432
DB_NAME = "entrevistas_db"
DB_USER = "entrevistas_db_user"
DB_PASS = "HPAJeAscIRX57UQWP71MGIJeJFI8SMLE"

def get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS
    )

# Carpeta para almacenar videos
app.config['UPLOAD_FOLDER'] = '/var/data/media'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Aumentamos el límite de subida a 50 MB
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# -----------------------------------------------------------------------------------
# FUNCIONES AUXILIARES
# -----------------------------------------------------------------------------------
def generar_clave_vacante(id_numerico):
    """
    Genera la clave única de la vacante.
    Formato: <id_numerico> + <7 caracteres alfanuméricos>.
    """
    random_str = ''.join(random.choices(string.ascii_letters + string.digits, k=7))
    return str(id_numerico) + random_str

# Clave usada para desencriptar



# Inicializar cliente de OpenAI
client = OpenAI(api_key=HARDCODED_API_KEY)


# -----------------------------------------------------------------------------------
# ESTILOS (UI/UX con fondo, negro, blanco, azul)
# -----------------------------------------------------------------------------------
STYLES = """
<style>
body {
    background: url('https://forhumancapital.mx/wp-content/uploads/2025/04/8.png') no-repeat center center fixed;
    background-size: cover;
    color: #FFFFFF;
    font-family: 'Segoe UI', Arial, sans-serif;
    margin: 0;
    padding: 0;
}
.container {
    max-width: 450px;
    margin: 80px auto;
    background-color: #1F1F1F;
    padding: 40px 30px;
    border-radius: 20px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.4);
}
.container-wide {
    max-width: 90%;
    margin: 50px auto;
    background-color: #1F1F1F;
    padding: 30px;
    border-radius: 20px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.4);
    overflow-x: auto;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 20px;
}
table th {
    background-color: #1E90FF;
    color: #fff;
}
table tr:hover {
    background-color: #2A2A2A;
    transition: background-color 0.3s;
}
table th, td {
    vertical-align: middle;
    padding: 8px;
    border: 1px solid #444;
}
table button {
    padding: 6px 10px;
    background-color: #1E90FF;
    color: #fff;
    border: none;
    border-radius: 8px;
    cursor: pointer;
    transition: background-color 0.3s, transform 0.2s;
}
table button:hover {
    background-color: #00BFFF;
    transform: scale(1.05);
}
.btn-ver {
    display: inline-block;
    padding: 6px 12px;
    background-color: #1E90FF;
    color: #fff;
    border-radius: 8px;
    text-decoration: none;
    font-weight: bold;
    transition: background-color 0.3s, transform 0.2s;
}
.btn-ver:hover {
    background-color: #00BFFF;
    transform: scale(1.05);
}
h1 {
    text-transform: uppercase;
    color: #00BFFF;
    text-align: center;
    margin-bottom: 30px;
}
h2 {
    color: white;
    text-align: center;
    margin-bottom: 30px;
}
.form-group {
    display: flex;
    flex-direction: column;
    margin-bottom: 15px;
}
.form-group label {
    margin-bottom: 4px;
    font-weight: bold;
}
.form-group input {
    padding: 10px;
    border-radius: 8px;
    border: 1px solid #ccc;
    background-color: #2A2A2A;
    color: #fff;
}
label {
    margin-bottom: 6px;
    font-weight: bold;
}
input[type="text"], input[type="email"], input[type="password"] {
    width: 100%;
    padding: 12px;
    border-radius: 10px;
    border: 1px solid #444;
    background-color: #2A2A2A;
    color: #FFF;
    margin-bottom: 20px;
    transition: border 0.3s, background-color 0.3s;
    font-size: 14px;
}
input[type="text"]:focus, input[type="email"]:focus, input[type="password"]:focus {
    border-color: #00BFFF;
    background-color: #333;
    outline: none;
}
button {
    width: 100%;
    padding: 14px;
    border: none;
    border-radius: 12px;
    background-color: #1E90FF;
    color: #fff;
    font-size: 16px;
    cursor: pointer;
    transition: background-color 0.3s, transform 0.2s;
}
button:hover {
    background-color: #00BFFF;
    transform: scale(1.02);
}
a {
    display: block;
    margin-top: 20px;
    text-align: center;
    color: #1E90FF;
    text-decoration: none;
    transition: color 0.3s;
}
a:hover {
    color: #00BFFF;
}
.file-upload {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 10px;
    margin-bottom: 20px;
}
.file-upload input[type="file"] {
    border: 1px solid #FFFFFF;
    border-radius: 10px;
    padding: 6px 10px;
    background-color: #2A2A2A;
    color: #FFFFFF;
    cursor: pointer;
}
.file-upload input[type="file"]::-webkit-file-upload-button {
    border: none;
    background: #1E90FF;
    color: #fff;
    padding: 8px 12px;
    border-radius: 8px;
    cursor: pointer;
    transition: background-color 0.3s;
}
.file-upload input[type="file"]::-webkit-file-upload-button:hover {
    background-color: #00BFFF;
}
.video-section {
    text-align: center;
    margin: 30px 0;
}
.video-section h3 {
    color: #00BFFF;
    margin-bottom: 20px;
}
.video-section video {
    width: 320px;
    height: 240px;
    background: #000;
    border-radius: 12px;
    margin: 10px auto;
    display: block;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
}
.video-section button {
    margin: 8px;
    padding: 10px 16px;
    border: none;
    border-radius: 10px;
    background-color: #1E90FF;
    color: #fff;
    cursor: pointer;
    transition: background-color 0.3s, transform 0.2s;
}
.video-section button:hover {
    background-color: #00BFFF;
    transform: scale(1.05);
}
.camera-select {
    margin: 15px 0;
}
.camera-select label {
    margin-right: 10px;
    font-weight: bold;
}
.camera-select select {
    padding: 8px;
    border-radius: 10px;
    border: 1px solid #fff;
    background-color: #2A2A2A;
    color: #fff;
}
.video-full {
    width: 100%;
    height: auto;
    max-height: 600px;
    background: #000;
    border-radius: 16px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    margin-top: 20px;
}
</style>
"""

# -----------------------------------------------------------------------------------
# TEMPLATES (HTML en cadenas)
# Se definen como cadenas normales (no f-string) para evitar interpretar las llaves de JS
# -----------------------------------------------------------------------------------
HOME_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Inicio</title>
""" + STYLES + """
</head>
<body>
<div class="container">
    <h1>Comienza tu entrevista aquí</h1>
    {% if session.get('usuario') %}
        <p>Has iniciado sesión como <strong>{{ session['usuario']['nombre'] }}</strong>.
           <a href="{{ url_for('logout') }}">Cerrar sesión</a></p>
        <hr>
        <h2>Opciones</h2>
        <ul>
            <li><a href="{{ url_for('dashboard') }}">Ver Vacantes / Crear Vacante</a></li>
        </ul>
    {% else %}
        <h2>Introduce la clave de la vacante para hacer la entrevista</h2>
        <form action="/check_clave" method="POST">
            <label>Clave de la vacante:</label><br>
            <input type="text" name="clave_vacante" value="{{ clave }}" required>
            <button type="submit">Ingresar</button>
        </form>
        <hr>
        <ul>
            <a href="{{ url_for('login') }}">Iniciar Sesión</a>
            <a href="{{ url_for('registro') }}">Registrarse</a>
        </ul>
    {% endif %}
</div>
</body>
</html>
"""
)

REGISTRO_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Registro</title>
""" + STYLES + """
</head>
<body>
<div class="container">
    <h1>Registro de Usuario</h1>
    <form method="POST" action="/registro">
        <label>Nombre:</label><br>
        <input type="text" name="nombre" required><br><br>
        <label>Correo:</label><br>
        <input type="email" name="correo" required><br><br>
        <label>Contraseña:</label><br>
        <input type="password" name="contraseña" required><br><br>
        <label>Ubicación:</label><br>
        <input type="text" name="ubicacion" required><br><br>
        <button type="submit">Registrarse</button>
    </form>
    <br>
    <a href="/">Volver al inicio</a>
</div>
</body>
</html>
"""
)

LOGIN_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Iniciar Sesión</title>
""" + STYLES + """
</head>
<body>
<div class="container">
    <h1>Iniciar Sesión</h1>
    <form method="POST" action="/login">
        <label>Correo:</label><br>
        <input type="email" name="correo" required><br><br>
        <label>Contraseña:</label><br>
        <input type="password" name="contraseña" required><br><br>
        <button type="submit">Ingresar</button>
    </form>
    <br>
    <a href="/">Volver al inicio</a>
</div>
</body>
</html>
"""
)

DASHBOARD_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Panel de Vacantes</title>
""" + STYLES + """
</head>
<body>
<div class="container-wide">
    <h1>Panel de Vacantes</h1>
    <h2>Bienvenido, {{ session['usuario']['nombre'] }}</h2>
    <h2>Lista de Vacantes</h2>
    <table>
        <tr>
            <th>Clave</th>
            <th>Empresa</th>
            <th>Puesto</th>
            <th>Fecha Publicación</th>
            <th>Ver Candidatos</th>
            <th>Copiar Enlace</th>
        </tr>
        {% for vac in vacantes %}
        <tr>
            <td>{{ vac['clave'] }}</td>
            <td>{{ vac['empresa'] }}</td>
            <td>{{ vac['puesto'] }}</td>
            <td>{{ vac['fecha_publicacion'] }}</td>
            <td><a href="/vacantes/{{ vac['id'] }}/candidatos" class="btn-ver">Ver</a></td>
            <td>
                <button onclick="copyLinkFromButton('https://for-video.onrender.com/?clave={{ vac['clave'] }}')">Copiar enlace</button>
            </td>
        </tr>
        {% endfor %}
    </table>
    <br>
    <a href="{{ url_for('crear_vacante') }}">Crear Nueva Vacante</a> |
    <a href="/">Volver al inicio</a>
</div>
<script>
function copyLinkFromButton(link) {
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(link).then(function() {
            alert('¡Enlace copiado al portapapeles!');
        }).catch(function(err) {
            alert('Error al copiar: ' + err);
        });
    } else {
        const tempTextArea = document.createElement("textarea");
        tempTextArea.value = link;
        document.body.appendChild(tempTextArea);
        tempTextArea.focus();
        tempTextArea.select();
        try {
            const successful = document.execCommand('copy');
            if (successful) {
                alert('¡Enlace copiado!');
            } else {
                alert('No se pudo copiar automáticamente.');
            }
        } catch (err) {
            alert('Error: ' + err);
        }
        document.body.removeChild(tempTextArea);
    }
}
</script>
</body>
</html>
"""
)

CREAR_VACANTE_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Crear Vacante</title>
""" + STYLES + """
</head>
<body>
<div class="container">
    <h1>Crear Nueva Vacante</h1>
    <form method="POST" action="/crear_vacante">
        <label>Empresa:</label><br>
        <input type="text" name="empresa" required><br><br>
        <label>Sucursal:</label><br>
        <input type="text" name="sucursal" required><br><br>
        <label>Puesto:</label><br>
        <input type="text" name="puesto" required><br><br>
        <label>Pregunta 1:</label><br>
        <input type="text" name="pregunta1" required><br><br>
        <label>Pregunta 2:</label><br>
        <input type="text" name="pregunta2" required><br><br>
        <label>Pregunta 3:</label><br>
        <input type="text" name="pregunta3" required><br><br>
        <button type="submit">Crear</button>
    </form>
    <br>
    <a href="{{ url_for('dashboard') }}">Volver al Panel</a>
</div>
</body>
</html>
"""
)

LISTA_CANDIDATOS_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Lista de Candidatos</title>
""" + STYLES + """
</head>
<body>
<div class="container-wide">
    <h1>Lista de Candidatos para</h1>
    <h2>{{ vacante['puesto'] }}</h2>
    <p><strong>Clave:</strong> {{ vacante['clave'] }}</p>
    <p><strong>Empresa:</strong> {{ vacante['empresa'] }} | <strong>Puesto:</strong> {{ vacante['puesto'] }}</p>
    <hr>
    <!-- Campo de búsqueda -->
    <div style="margin-bottom: 20px;">
        <input type="text" id="searchInput" placeholder="Buscar por nombre, correo o celular..." onkeyup="filterTable()" style="width: 100%; padding: 10px; border-radius: 10px; border: 1px solid #ccc;">
    </div>
    {% if candidatos %}
    <table id="candidatosTable">
        <tr>
            <th>ID</th>
            <th>Nombre Completo</th>
            <th>Correo</th>
            <th>Celular</th>
            <th>Ver Entrevista</th>
        </tr>
        {% for c in candidatos %}
        <tr>
            <td>{{ c['id'] }}</td>
            <td>{{ c['nombre_completo'] }}</td>
            <td>{{ c['correo'] }}</td>
            <td>{{ c['celular'] }}</td>
            <td><a href="/vacantes/{{ vacante['id'] }}/candidatos/{{ c['id'] }}">Ver Entrevista</a></td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
    <p>No hay candidatos registrados para esta vacante.</p>
    {% endif %}
    <br>
    <a href="{{ url_for('dashboard') }}">Volver al Panel</a>
</div>
<script>
function filterTable() {
    const input = document.getElementById('searchInput');
    const filter = input.value.toLowerCase();
    const table = document.getElementById('candidatosTable');
    const tr = table.getElementsByTagName('tr');
    for (let i = 1; i < tr.length; i++) {
        let rowText = tr[i].textContent.toLowerCase();
        if (rowText.includes(filter)) {
            tr[i].style.display = '';
        } else {
            tr[i].style.display = 'none';
        }
    }
}
</script>
</body>
</html>
"""
)

FORM_CANDIDATO_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Formulario de Candidato</title>
""" + STYLES + """
</head>
<body>
<div class="container">
    <h1>FORMULARIO DE CANDIDATO</h1>
    <h1>{{ vacante['empresa'] }} - {{ vacante['puesto'] }}</h1>
    <form id="candidateForm">
        <input type="hidden" name="id_vacante" value="{{ vacante['id'] }}">
        <label>Nombre completo:</label><br>
        <input type="text" name="nombre_completo" required><br><br>
        <label>Correo electrónico:</label><br>
        <input type="email" name="correo" required><br><br>
        <label>Celular:</label><br>
        <input type="text" name="celular" required><br><br>
        <hr>
        <h2><strong>{{ vacante['pregunta1'] }}</strong></h2>
        <h2><strong>{{ vacante['pregunta2'] }}</strong></h2>
        <h2><strong>{{ vacante['pregunta3'] }}</strong></h2>
        <div class="file-upload">
            <label for="video_upload">Subir video:</label>
            <input type="file" id="video_upload" name="video_upload" accept="video/*">
            
        </div>
        <div class="video-section">
            <h3>O Graba tu video (5 min máx, ~720p, 20fps)</h3>
            <video id="preview" autoplay muted></video><br>
            <button type="button" onclick="turnOnCamera()">Encender cámara</button>
            <div class="camera-select">
                <label for="cameraSelect">Selecciona cámara:</label>
                <select id="cameraSelect"></select>
            </div>
            <button type="button" onclick="startRecording()">Comenzar grabación</button>
            <button type="button" onclick="stopRecording()">Detener grabación</button>
            <video id="recordedVideo" controls style="display:none;"></video>
            <button type="submit">Enviar</button>
        </div>
    </form>
</div>
<script>
let mediaRecorder;
let recordedChunks = [];
let recordedBlob = null;
let stream = null;
async function turnOnCamera() {
    const selectedCameraId = document.getElementById('cameraSelect').value;
    if (!stream) {
        const constraints = {
            video: {
                deviceId: selectedCameraId ? { exact: selectedCameraId } : undefined,
                width: { ideal: 720 },
                height: { ideal: 720 },
                frameRate: { ideal: 20 }
            },
            audio: true
        };
        try {
            stream = await navigator.mediaDevices.getUserMedia(constraints);
            document.getElementById('preview').srcObject = stream;
            alert("Cámara encendida.");
        } catch (error) {
            alert("Error al encender la cámara: " + error);
        }
    } else {
        alert("La cámara ya está encendida.");
    }
}
async function populateCameraList() {
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const cameras = devices.filter(device => device.kind === 'videoinput');
        const cameraSelect = document.getElementById('cameraSelect');
        cameraSelect.innerHTML = '';
        cameras.forEach((camera, index) => {
            const option = document.createElement('option');
            option.value = camera.deviceId;
            option.text = camera.label || "Cámara " + (index + 1);
            cameraSelect.appendChild(option);
        });
    } catch (error) {
        alert("No se pudieron listar las cámaras: " + error);
    }
}
function startRecording() {
    if (!stream) {
        alert("Primero debes encender la cámara.");
        return;
    }
    recordedChunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => {
        if (e.data.size > 0) recordedChunks.push(e.data);
    };
    mediaRecorder.start();
    setTimeout(() => {
        if (mediaRecorder.state !== 'inactive') stopRecording();
    }, 300000);
    alert("¡Grabando!");
}
function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
        mediaRecorder.onstop = () => {
            recordedBlob = new Blob(recordedChunks, { type: 'video/webm' });
            const videoURL = URL.createObjectURL(recordedBlob);
            const recordedVideo = document.getElementById('recordedVideo');
            recordedVideo.src = videoURL;
            recordedVideo.style.display = 'block';
        };
    }
    const preview = document.getElementById('preview');
    if (preview.srcObject) {
        preview.srcObject.getTracks().forEach(track => track.stop());
    }
    preview.srcObject = null;
}
document.getElementById('candidateForm').addEventListener('submit', async function(e) {
    e.preventDefault();
    const formData = new FormData();
    formData.append('id_vacante', document.querySelector('input[name="id_vacante"]').value);
    formData.append('nombre_completo', document.querySelector('input[name="nombre_completo"]').value);
    formData.append('correo', document.querySelector('input[name="correo"]').value);
    formData.append('celular', document.querySelector('input[name="celular"]').value);
    const fileInput = document.getElementById('video_upload');
    if (fileInput.files.length > 0) {
        formData.append('video', fileInput.files[0]);
    } else if (recordedBlob) {
        formData.append('video', recordedBlob, 'grabado.webm');
    } else {
        alert("Por favor, sube un video o graba uno antes de enviar.");
        return;
    }
    try {
        const response = await fetch('/registrar_candidato', {
            method: 'POST',
            body: formData
        });
        const result = await response.text();
        alert(result);
        window.location.href = '/';
    } catch(error) {
        alert("Error al enviar formulario: " + error);
    }
});
window.addEventListener('load', populateCameraList);
</script>
</body>
</html>
"""
)

ENTREVISTA_CANDIDATO_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Entrevista Candidato</title>
    """ + STYLES + """
</head>
<body>
<div class="container-wide">
    <h1>Entrevista de {{ candidato['nombre_completo'] }}</h1>
    <p><strong>Correo:</strong> {{ candidato['correo'] }}</p>
    <p><strong>Celular:</strong> {{ candidato['celular'] }}</p>
    <hr>
    <h2>Datos de la Vacante</h2>
    <p><strong>Empresa:</strong> {{ vacante['empresa'] }}</p>
    <p><strong>Puesto:</strong> {{ vacante['puesto'] }}</p>
    <hr>
    <h2>Preguntas de la Vacante</h2>
    <p>{{ vacante['pregunta1'] }}</p>
    <p>{{ vacante['pregunta2'] }}</p>
    <p>{{ vacante['pregunta3'] }}</p>
    <hr>
    <h2>Video de la Entrevista</h2>
    <video controls class="video-full">
        <source src="{{ candidato['ruta_video'] }}" type="video/webm">
        Tu navegador no soporta video.
    </video>
    <br>
    <a href="{{ candidato['ruta_video'] }}" download class="btn-ver">Descargar Video</a>


    <hr>
    <h2>Transcripción Segmentada (cada ~30 segundos)</h2>
    <div style="background-color: #2A2A2A; color: #FFFFFF; padding: 15px; border-radius: 8px; white-space: pre-wrap;">
        {{ candidato['transcripcion'] }}
    </div>

    <hr>
    <h2>Informe del Candidato (Generado por ChatGPT)</h2>
    <div style="background-color: #2A2A2A; color: #FFFFFF; padding: 15px; border-radius: 8px; white-space: pre-wrap;">
        {{ candidato['informe'] }}
    </div>

    <br>
    <a href="/vacantes/{{ vacante_id }}/candidatos">Volver a la lista</a>
</div>
</body>
</html>

"""
)

VACANTE_CREADA_TEMPLATE = (
    """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Vacante creada</title>
""" + STYLES + """
</head>
<body>
<div class="container">
    <h1>¡Vacante generada con éxito!</h1>
    <p>Se ha generado la siguiente clave:</p>
    <h2>{{ clave }}</h2>
    <p>Este es el enlace para compartir la vacante:</p>
    <div class="copy-link-container">
        <input type="text" id="linkVacante" value="https://for-video.onrender.com//?clave={{ clave }}" readonly>
        <button onclick="copyLink()">Copiar enlace</button>
    </div>
    <br>
    <a href="/dashboard">Volver al panel</a>
</div>
<script>
function copyLink() {
    var copyText = document.getElementById("linkVacante");
    copyText.select();
    copyText.setSelectionRange(0, 99999);
    document.execCommand("copy");
    alert("¡Enlace copiado!");
}
</script>
<style>
.copy-link-container {
    display: flex;
    gap: 10px;
    align-items: center;
}
.copy-link-container input {
    width: 100%;
    padding: 10px;
    border-radius: 8px;
    border: 1px solid #ccc;
    background-color: #2A2A2A;
    color: #fff;
}
.copy-link-container button {
    padding: 10px 14px;
    background-color: #1E90FF;
    border: none;
    border-radius: 8px;
    color: #fff;
    cursor: pointer;
    transition: background-color 0.3s;
}
.copy-link-container button:hover {
    background-color: #00BFFF;
}
</style>
</body>
</html>
"""
)

# -----------------------------------------------------------------------------------
# RUTAS
# -----------------------------------------------------------------------------------
@app.route('/')
def home():
    clave = request.args.get('clave', '')
    return render_template_string(HOME_TEMPLATE, clave=clave)

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if request.method == 'POST':
        nombre = request.form.get('nombre')
        correo = request.form.get('correo')
        contraseña = request.form.get('contraseña')
        ubicacion = request.form.get('ubicacion')
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT * FROM usuarios WHERE correo = %s", (correo,))
        existe = cur.fetchone()
        if existe:
            cur.close()
            conn.close()
            return "Error: Este correo ya está registrado. <br><a href='/'>Volver</a>"
        cur.execute("""
            INSERT INTO usuarios(nombre, correo, contraseña, ubicacion)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """, (nombre, correo, contraseña, ubicacion))
        conn.commit()
        cur.close()
        conn.close()
        return "Usuario registrado con éxito. <br><a href='/'>Ir al inicio</a>"
    else:
        return render_template_string(REGISTRO_TEMPLATE)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        correo = request.form.get('correo')
        contraseña = request.form.get('contraseña')
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            SELECT id, nombre, correo FROM usuarios
            WHERE correo = %s AND contraseña = %s
        """, (correo, contraseña))
        usuario = cur.fetchone()
        cur.close()
        conn.close()
        if usuario:
            session['usuario'] = {
                'id': usuario['id'],
                'nombre': usuario['nombre'],
                'correo': usuario['correo']
            }
            return redirect(url_for('dashboard'))
        else:
            return "Credenciales inválidas. <br><a href='/login'>Intentar de nuevo</a>"
    else:
        return render_template_string(LOGIN_TEMPLATE)

@app.route('/logout')
def logout():
    session.pop('usuario', None)
    return redirect('/')

@app.route('/dashboard')
def dashboard():
    if 'usuario' not in session:
        return redirect('/')
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM vacantes ORDER BY id DESC")
    vac_rows = cur.fetchall()
    cur.close()
    conn.close()
    vacantes = []
    for v in vac_rows:
        vacantes.append({
            'id': v['id'],
            'clave': v['clave'],
            'empresa': v['empresa'],
            'sucursal': v['sucursal'],
            'puesto': v['puesto'],
            'fecha_publicacion': v['fecha_publicacion'].strftime("%Y-%m-%d %H:%M:%S") if v['fecha_publicacion'] else ''
        })
    return render_template_string(DASHBOARD_TEMPLATE, vacantes=vacantes)

@app.route('/crear_vacante', methods=['GET','POST'])
def crear_vacante():
    if 'usuario' not in session:
        return redirect('/')
    if request.method == 'POST':
        empresa = request.form.get('empresa')
        sucursal = request.form.get('sucursal')
        puesto = request.form.get('puesto')
        pregunta1 = request.form.get('pregunta1')
        pregunta2 = request.form.get('pregunta2')
        pregunta3 = request.form.get('pregunta3')
        fecha_publicacion = datetime.now()
        conn = get_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("""
            INSERT INTO vacantes(empresa, sucursal, puesto, fecha_publicacion,
                                 pregunta1, pregunta2, pregunta3)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (empresa, sucursal, puesto, fecha_publicacion, pregunta1, pregunta2, pregunta3))
        new_id = cur.fetchone()['id']
        conn.commit()
        clave = generar_clave_vacante(new_id)
        cur.execute("UPDATE vacantes SET clave = %s WHERE id = %s", (clave, new_id))
        conn.commit()
        cur.close()
        conn.close()
        return render_template_string(VACANTE_CREADA_TEMPLATE, clave=clave)
    else:
        return render_template_string(CREAR_VACANTE_TEMPLATE)

@app.route('/vacantes/<int:vacante_id>/candidatos/<int:candidato_id>')
def ver_entrevista_candidato(vacante_id, candidato_id):
    if 'usuario' not in session:
        return redirect('/')
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT * FROM candidatos WHERE id = %s", (candidato_id,))
    cand_row = cur.fetchone()
    if not cand_row:
        cur.close()
        conn.close()
        return "El candidato no existe."

    candidato = {
        'id': cand_row['id'],
        'nombre_completo': cand_row['nombre_completo'],
        'correo': cand_row['correo'],
        'celular': cand_row['celular'],
        'ruta_video': cand_row['ruta_video'],
        'transcripcion': cand_row['transcripcion'], # Añadido
        'informe': cand_row['informe']              # Añadido
    }

    cur.execute("SELECT * FROM vacantes WHERE id = %s", (vacante_id,))
    vac_row = cur.fetchone()
    if not vac_row:
        cur.close()
        conn.close()
        return "La vacante no existe."

    vacante = {
        'id': vac_row['id'],
        'clave': vac_row['clave'],
        'empresa': vac_row['empresa'],
        'sucursal': vac_row['sucursal'],
        'puesto': vac_row['puesto'],
        'fecha_publicacion': vac_row['fecha_publicacion'],
        'pregunta1': vac_row['pregunta1'],
        'pregunta2': vac_row['pregunta2'],
        'pregunta3': vac_row['pregunta3']
    }

    cur.close()
    conn.close()

    return render_template_string(
        ENTREVISTA_CANDIDATO_TEMPLATE,
        candidato=candidato,
        vacante_id=vacante_id,
        vacante=vacante
    )



@app.route('/check_clave', methods=['POST'])
def check_clave():
    clave_vacante = request.form.get('clave_vacante')
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT id FROM vacantes WHERE clave = %s", (clave_vacante,))
    vac_row = cur.fetchone()
    cur.close()
    conn.close()
    if not vac_row:
        return "Clave de vacante inválida. <br><a href='/'>Volver</a>"
    else:
        return redirect(url_for('form_candidato', vacante_id=vac_row['id']))

@app.route('/entrevista/<int:vacante_id>')
def form_candidato(vacante_id):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute("SELECT * FROM vacantes WHERE id = %s", (vacante_id,))
    vac_row = cur.fetchone()
    cur.close()
    conn.close()
    if not vac_row:
        return "Vacante no existe. <br><a href='/'>Volver</a>"
    vacante = {
        'id': vac_row['id'],
        'clave': vac_row['clave'],
        'empresa': vac_row['empresa'],
        'sucursal': vac_row['sucursal'],
        'puesto': vac_row['puesto'],
        'fecha_publicacion': vac_row['fecha_publicacion'],
        'pregunta1': vac_row['pregunta1'],
        'pregunta2': vac_row['pregunta2'],
        'pregunta3': vac_row['pregunta3']
    }
    return render_template_string(FORM_CANDIDATO_TEMPLATE, vacante=vacante)

@app.route('/registrar_candidato', methods=['POST'])
def registrar_candidato():
    print(">>> FORMULARIO RECIBIDO")
    print("Form keys:", list(request.form.keys()))
    print("File keys:", list(request.files.keys()))
    print("File 'video':", request.files.get('video'))

    id_vacante = request.form.get('id_vacante')
    nombre_completo = request.form.get('nombre_completo')
    correo = request.form.get('correo')
    celular = request.form.get('celular')
    file_video = request.files.get('video')

    if not file_video:
        return f"No se recibió ningún video. <br><a href='/entrevista/{id_vacante}'>Intentar de nuevo</a>"

    file_video.seek(0, os.SEEK_END)
    file_size = file_video.tell()
    file_video.seek(0, 0)

    if file_size > 50 * 1024 * 1024:
        return "El video excede el tamaño máximo de 50MB. <br><a href='/'>Volver</a>"

    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cur.execute("SELECT clave, puesto FROM vacantes WHERE id = %s", (id_vacante,))
    row = cur.fetchone()

    if not row:
        cur.close()
        conn.close()
        return "Vacante inexistente."

    clave_vacante = row['clave']
    puesto_vacante = row['puesto']

    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    original_ext = os.path.splitext(file_video.filename)[1].lower()
    original_filename = secure_filename(f"{timestamp}_{clave_vacante}{original_ext}")
    original_path = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
    file_video.save(original_path)

    # Convertir a WebM si no lo es ya
    if not original_filename.endswith('.webm'):
        webm_filename = f"{timestamp}_{clave_vacante}.webm"
        webm_path = os.path.join(app.config['UPLOAD_FOLDER'], webm_filename)
        try:
            subprocess.run([
                "ffmpeg", "-i", original_path,
                "-c:v", "libvpx-vp9", "-b:v", "1M",
                "-c:a", "libopus",
                "-vf", "scale=720:-1",
                webm_path
            ], check=True)
            os.remove(original_path)  # eliminar original
            final_path = webm_path
            nombre_video = webm_filename
            print("✅ Video convertido a WebM con éxito.")
        except Exception as e:
            print("⚠️ Error al convertir a WebM:", e)
            final_path = original_path
            nombre_video = original_filename
    else:
        final_path = original_path
        nombre_video = original_filename

    #ruta_guardada = f"/{app.config['UPLOAD_FOLDER']}/{nombre_video}"
    ruta_guardada = f"/videos/{nombre_video}"


    # Verifica cliente OpenAI
    if client is None:
        return "Error: Cliente OpenAI no inicializado."

    # Generar transcripción segmentada
    transcripcion_segmentada = transcribir_audio_por_segmentos(client, final_path)

    # Generar informe usando ChatGPT
    informe_candidato = generar_informe_chatgpt(client, puesto_vacante, transcripcion_segmentada)

    # Guardar candidato con transcripción e informe en la DB
    cur.execute("""
        INSERT INTO candidatos(nombre_completo, correo, celular, id_vacante,
                               nombre_video, ruta_video, transcripcion, informe)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (nombre_completo, correo, celular, id_vacante, nombre_video, ruta_guardada,
          transcripcion_segmentada, informe_candidato))

    conn.commit()
    cur.close()
    conn.close()

    return "¡Formulario y video enviados con éxito!"


@app.route('/videos/<path:filename>')
def download_video(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/vacantes/<int:vacante_id>/candidatos')
def lista_candidatos(vacante_id):
    if 'usuario' not in session:
        return redirect('/')
    
    conn = get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    # Obtener la vacante
    cur.execute("SELECT * FROM vacantes WHERE id = %s", (vacante_id,))
    vac_row = cur.fetchone()
    if not vac_row:
        cur.close()
        conn.close()
        return "La vacante no existe."

    vacante = {
        'id': vac_row['id'],
        'clave': vac_row['clave'],
        'empresa': vac_row['empresa'],
        'sucursal': vac_row['sucursal'],
        'puesto': vac_row['puesto'],
        'fecha_publicacion': vac_row['fecha_publicacion'],
        'pregunta1': vac_row['pregunta1'],
        'pregunta2': vac_row['pregunta2'],
        'pregunta3': vac_row['pregunta3']
    }

    # Obtener candidatos
    cur.execute("SELECT * FROM candidatos WHERE id_vacante = %s ORDER BY id DESC", (vacante_id,))
    rows = cur.fetchall()

    candidatos = []
    for row in rows:
        candidatos.append({
            'id': row['id'],
            'nombre_completo': row['nombre_completo'],
            'correo': row['correo'],
            'celular': row['celular']
        })

    cur.close()
    conn.close()

    return render_template_string(LISTA_CANDIDATOS_TEMPLATE, vacante=vacante, candidatos=candidatos)


# -----------------------------------------------------------------------------------
# EJECUCIÓN
# -----------------------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True)
