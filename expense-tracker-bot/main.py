import os
import json
import re
import base64
import pandas as pd
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from huggingface_hub import InferenceClient
from dotenv import load_dotenv
import requests

# Cargar .env
load_dotenv()

# Config
with open('config_telegram_expense.json', 'r') as f:
    config = json.load(f)
ALLOWED_CHATS = [config['telegram_chat_id']] if config['telegram_chat_id'] else []

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
REPO = 'estudiochapunov/expense-tracker-bot'
CSV_FILE = 'gastos.csv'

# Cliente HF para OCR
hf_client = InferenceClient(model="microsoft/trocr-base-printed")

def parse_gasto(texto):
    # Parse simple: Busca fecha (dd/mm/yyyy o yyyy-mm-dd), monto ($numero), categoria (palabras clave)
    fecha_match = re.search(r'\b(\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\b', texto)
    fecha = fecha_match.group(1) if fecha_match else '2025-11-25'
    monto_match = re.search(r'\$?(\d+(?:,\d{3})*(?:\.\d{2})?)', texto.replace(',', ''))
    monto = float(monto_match.group(1).replace(',', '')) if monto_match else 0
    categoria = 'general'
    if 'supermercado' in texto.lower() or 'coto' in texto.lower():
        categoria = 'supermercado'
    elif 'farmacia' in texto.lower():
        categoria = 'farmacia'
    # Agregar más keywords
    descripcion = texto
    return {'fecha': fecha, 'monto': monto, 'categoria': categoria, 'descripcion': descripcion}

def guardar_en_github(entry):
    url = f"https://api.github.com/repos/{REPO}/contents/{CSV_FILE}"
    headers = {'Authorization': f'token {GITHUB_TOKEN}'}
    response = requests.get(url, headers=headers)
    sha = None
    if response.status_code == 200:
        content = response.json()
        csv_content = pd.read_csv(base64.b64decode(content['content']))
        sha = content.get('sha')
    else:
        csv_content = pd.DataFrame(columns=['fecha', 'monto', 'categoria', 'descripcion'])
    
    new_row = pd.DataFrame([entry])
    csv_content = pd.concat([csv_content, new_row], ignore_index=True)
    
    csv_str = csv_content.to_csv(index=False)
    encoded = base64.b64encode(csv_str.encode()).decode()
    data = {'message': 'Nuevo gasto', 'content': encoded}
    if sha:
        data['sha'] = sha
    requests.put(url, headers=headers, json=data)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if update.effective_chat.id not in ALLOWED_CHATS:
        await update.message.reply_text('No autorizado.')
        return
    await update.message.reply_text('Bot listo. Envía imagen de ticket o texto transcrito. Comandos: /gastos fecha:YYYY-MM-DD, /gastos categoria:nombre, /resumen desde:YYYY-MM-DD hasta:YYYY-MM-DD')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if update.effective_chat.id not in ALLOWED_CHATS:
        return
    
    texto = ''
    if update.message.photo:
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        img_bytes = await file.download_as_bytearray()
        # OCR con HF
        result = hf_client.text_generation(img_bytes, prompt="Extrae texto del ticket de gasto.")
        texto = result[0]['generated_text']
    elif update.message.text:
        texto = update.message.text
    
    if texto:
        entry = parse_gasto(texto)
        guardar_en_github(entry)
        await update.message.reply_text(f'Guardado: ${entry["monto"]} en {entry["categoria"]} el {entry["fecha"]}')

async def cmd_gastos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if update.effective_chat.id not in ALLOWED_CHATS:
        await update.message.reply_text('No autorizado.')
        return
    
    if not context.args:
        await update.message.reply_text('Uso: /gastos fecha:YYYY-MM-DD o categoria:nombre o desde:YYYY-MM-DD hasta:YYYY-MM-DD')
        return
    query = ' '.join(context.args)
    # Leer CSV
    url = f"https://api.github.com/repos/{REPO}/contents/{CSV_FILE}"
    headers = {'Authorization': f'token {GITHUB_TOKEN}'}
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        await update.message.reply_text('Error al leer datos.')
        return
    csv_content = pd.read_csv(base64.b64decode(response.json()['content']))
    
    if 'fecha:' in query:
        fecha = query.split('fecha:')[1]
        filtered = csv_content[csv_content['fecha'] == fecha]
        if filtered.empty:
            await update.message.reply_text('No hay gastos esa fecha.')
        else:
            msg = '\n'.join([f'{row["monto"]} - {row["categoria"]} - {row["descripcion"]}' for _, row in filtered.iterrows()])
            await update.message.reply_text(f'Gastos el {fecha}:\n{msg}')
    elif 'categoria:' in query:
        cat = query.split('categoria:')[1]
        filtered = csv_content[csv_content['categoria'].str.lower() == cat.lower()]
        total = filtered['monto'].sum()
        await update.message.reply_text(f'Total en {cat}: ${total}')
    elif 'desde:' in query and 'hasta:' in query:
        desde = query.split('desde:')[1].split(' ')[0]
        hasta = query.split('hasta:')[1]
        filtered = csv_content[(csv_content['fecha'] >= desde) & (csv_content['fecha'] <= hasta)]
        total = filtered['monto'].sum()
        await update.message.reply_text(f'Total desde {desde} hasta {hasta}: ${total}')
    else:
        await update.message.reply_text('Uso: /gastos fecha:YYYY-MM-DD o categoria:nombre o desde:YYYY-MM-DD hasta:YYYY-MM-DD')

# Main
app = Application.builder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler('start', start))
app.add_handler(CommandHandler('gastos', cmd_gastos))
app.add_handler(MessageHandler(filters.PHOTO | filters.TEXT, handle_message))
app.run_polling()