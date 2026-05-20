import os
import re
import requests
import urllib.parse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

MARCA, MODELO, ANIO = range(3)
ALERTA_MARCA, ALERTA_MODELO, ALERTA_ANIO = range(3, 6)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'AutoROI Bot corriendo')
    def log_message(self, format, *args):
        pass

def run_server():
    server = HTTPServer(('0.0.0.0', 8080), Handler)
    server.serve_forever()

def obtener_dolar_blue():
    try:
        response = requests.get("https://api.bluelytics.com.ar/v2/latest", timeout=5)
        data = response.json()
        return data['blue']['value_sell']
    except Exception as e:
        print(f"Error obteniendo dólar: {e}")
        return None

def limpiar_link(link):
    if 'click1.mercadolibre' in link or 'click2.mercadolibre' in link:
        params = urllib.parse.urlparse(link)
        query = urllib.parse.parse_qs(params.query)
        if 'url' in query:
            link = query['url'][0]
    if '#' in link:
        link = link.split('#')[0]
    return link

def obtener_semaforo(analisis_texto):
    try:
        match = re.search(r'Margen estimado[:\s]+\$([0-9\.]+)', analisis_texto)
        if match:
            margen = float(match.group(1).replace('.', ''))
            if margen >= 2000000:
                return '🟢'
            elif margen >= 500000:
                return '🟡'
            else:
                return '🔴'
    except:
        pass
    return '🟡'

def buscar_auto_existente(link):
    try:
        url = f"{SUPABASE_URL}/rest/v1/auto?link=eq.{urllib.parse.quote(link)}&order=fecha.desc&limit=1"
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        response = requests.get(url, headers=headers)
        data = response.json()
        if data and len(data) > 0:
            return data[0]
        return None
    except Exception as e:
        print(f"Error buscando auto existente: {e}")
        return None

def guardar_auto(titulo, precio, link, anio, km):
    url = f"{SUPABASE_URL}/rest/v1/auto"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    data = {"titulo": titulo, "precio": precio, "link": link, "fecha": datetime.now(timezone.utc).isoformat()}
    response = requests.post(url, json=data, headers=headers)
    print(f"Supabase: {response.status_code} - {response.text}")

def detectar_baja_precio(titulo, precio_actual, link):
    existente = buscar_auto_existente(link)
    if existente:
        precio_anterior = existente.get('precio', 0)
        if precio_anterior and precio_actual < precio_anterior:
            diferencia = precio_anterior - precio_actual
            porcentaje = (diferencia / precio_anterior) * 100
            return {'bajo': True, 'precio_anterior': precio_anterior, 'diferencia': diferencia, 'porcentaje': porcentaje}
    return {'bajo': False}

def guardar_alerta(chat_id, marca, modelo, anio):
    url = f"{SUPABASE_URL}/rest/v1/alertas"
    headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"}
    data = {"chat_id": str(chat_id), "marca": marca, "modelo": modelo, "anio": anio}
    response = requests.post(url, json=data, headers=headers)
    return response.status_code == 201

def obtener_alertas():
    try:
        url = f"{SUPABASE_URL}/rest/v1/alertas?select=*"
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        response = requests.get(url, headers=headers)
        return response.json()
    except Exception as e:
        print(f"Error obteniendo alertas: {e}")
        return []

def obtener_alertas_usuario(chat_id):
    try:
        url = f"{SUPABASE_URL}/rest/v1/alertas?chat_id=eq.{chat_id}&select=*"
        headers = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
        response = requests.get(url, headers=headers)
        return response.json()
    except Exception as e:
        print(f"Error obteniendo alertas usuario: {e}")
        return []

def filtrar_y_analizar(query, items_data, dolar_blue):
    url = "https://api.anthropic.com/v1/messages"
    headers = {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    items_texto = ""
    for i, item in enumerate(items_data):
        items_texto += f"{i+1}. {item['titulo']} | {item['anio']} | {item['km']} km | {item['moneda']} {item['precio']} | {item['motor']} | {item['version']}\n"
    dolar_texto = f"El dólar blue hoy cotiza a ${dolar_blue} ARS por USD." if dolar_blue else "No se pudo obtener el tipo de cambio blue actual, usá una referencia aproximada."
    data = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": f"""Sos un experto en compraventa de autos usados en Argentina con amplio conocimiento del mercado.

{dolar_texto}
Usá SIEMPRE este tipo de cambio para convertir USD a pesos.

El usuario buscó: '{query}'
Resultados de Mercado Libre:

{items_texto}

Elegí los 3 mejores para hacer negocio de compraventa o consignación.
Basate en tu conocimiento del mercado argentino para estimar el valor de referencia.
Siempre respondé con estimaciones, nunca te niegues.

Respondé SOLO en este formato, sin texto extra:

NUMERO: [número]
ANALISIS: [Valor ref. mercado: $X ARS. Precio publicado: $Y ARS. Margen estimado: $Z ARS. Una oración si vale la pena.]
---

NUMERO: [número]
ANALISIS: [Valor ref. mercado: $X ARS. Precio publicado: $Y ARS. Margen estimado: $Z ARS. Una oración si vale la pena.]
---

NUMERO: [número]
ANALISIS: [Valor ref. mercado: $X ARS. Precio publicado: $Y ARS. Margen estimado: $Z ARS. Una oración si vale la pena.]
---
"""}]
    }
    try:
        response = requests.post(url, json=data, headers=headers)
        result = response.json()
        return result['content'][0]['text']
    except Exception as e:
        print(f"Error Claude: {e}")
        return None

def parsear_respuesta_claude(respuesta, items_data):
    seleccionados = []
    bloques = respuesta.strip().split('---')
    for bloque in bloques:
        bloque = bloque.strip()
        if not bloque:
            continue
        try:
            lineas = bloque.split('\n')
            numero = None
            analisis = None
            for linea in lineas:
                if linea.startswith('NUMERO:'):
                    numero = int(linea.replace('NUMERO:', '').strip()) - 1
                elif linea.startswith('ANALISIS:'):
                    analisis = linea.replace('ANALISIS:', '').strip()
            if numero is not None and analisis and 0 <= numero < len(items_data):
                seleccionados.append({**items_data[numero], 'analisis': analisis})
        except:
            continue
    return seleccionados

async def scrapear_meli(query):
    from playwright.async_api import async_playwright
    from bs4 import BeautifulSoup

    url = f"https://autos.mercadolibre.com.ar/{query.replace(' ', '-').lower()}/"
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled']
        )
        ctx = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            locale='es-AR',
            extra_http_headers={'Accept-Language': 'es-AR,es;q=0.9'}
        )
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(8000)
        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, 'html.parser')
    items = soup.select('.poly-card')[:8]
    items_data = []
    for item in items:
        titulo = item.select_one('.poly-component__title')
        precio = item.select_one('.andes-money-amount__fraction')
        moneda = item.select_one('.andes-money-amount__currency-symbol')
        link = item.select_one('a')
        atributos = item.select('.poly-attributes_list__item')
        todos = [a.text.strip() for a in atributos]
        link_limpio = limpiar_link(link['href']) if link else ''
        items_data.append({
            'titulo': titulo.text.strip()[:60] if titulo else 'Sin título',
            'precio': precio.text.strip() if precio else '0',
            'moneda': moneda.text.strip() if moneda else 'ARS',
            'link': link_limpio[:150],
            'anio': todos[0] if len(todos) > 0 else 'N/A',
            'km': todos[1] if len(todos) > 1 else 'N/A',
            'motor': todos[2] if len(todos) > 2 else 'N/A',
            'version': todos[3] if len(todos) > 3 else 'N/A',
        })
    return items_data

async def ejecutar_busqueda(app, chat_id, marca, modelo, anio):
    if anio.lower() == 'cualquiera':
        query = f"{marca} {modelo}"
    else:
        query = f"{marca} {modelo} {anio}"

    try:
        items_data = await scrapear_meli(query)
        if not items_data:
            return

        dolar_blue = obtener_dolar_blue()
        respuesta_claude = filtrar_y_analizar(query, items_data, dolar_blue)
        if not respuesta_claude:
            return

        seleccionados = parsear_respuesta_claude(respuesta_claude, items_data)
        if not seleccionados:
            return

        mensaje = f"🔔 Alerta programada para '{query}':\n\n"
        tiene_novedad = False

        for auto in seleccionados:
            try:
                precio_num = float(auto['precio'].replace('.', '').replace(',', '.'))
            except:
                precio_num = 0

            baja = detectar_baja_precio(auto['titulo'], precio_num, auto['link'])
            guardar_auto(auto['titulo'], precio_num, auto['link'], auto['anio'], auto['km'])
            semaforo = obtener_semaforo(auto['analisis'])

            if baja['bajo'] or semaforo == '🟢':
                tiene_novedad = True

            alerta_baja = f"⚠️ BAJÓ DE PRECIO: antes ${baja['precio_anterior']:,.0f} → ahora ${precio_num:,.0f} (-{baja['porcentaje']:.1f}%)\n" if baja['bajo'] else ""
            mensaje += f"{semaforo} {auto['titulo']}\n📅 {auto['anio']} | 🛣️ {auto['km']}\n💰 {auto['moneda']} {auto['precio']}\n"
            if alerta_baja:
                mensaje += alerta_baja
            mensaje += f"🔗 {auto['link']}\n📊 {auto['analisis']}\n\n"

        if tiene_novedad:
            await app.bot.send_message(chat_id=chat_id, text=mensaje)
    except Exception as e:
        print(f"Error en alerta programada: {e}")

async def job_alertas(app):
    print("Ejecutando alertas programadas...")
    alertas = obtener_alertas()
    for alerta in alertas:
        await ejecutar_busqueda(app, alerta['chat_id'], alerta['marca'], alerta['modelo'], alerta['anio'])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¡Hola! Soy AutoROI 🚗. Estoy listo para buscar oportunidades de compraventa.\n\nUsá /buscar para arrancar o /ayuda para ver cómo funciono.")

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Cómo usar AutoROI\n\n"
        "1. /buscar — búsqueda manual\n"
        "2. /alerta — guardar búsqueda automática cada 6hs\n"
        "3. /mis_alertas — ver tus alertas activas\n\n"
        "Semáforo:\n"
        "🟢 Margen mayor a $2.000.000\n"
        "🟡 Margen entre $500.000 y $2.000.000\n"
        "🔴 Margen menor a $500.000\n\n"
        "Para cancelar escribí /cancelar"
    )

async def mis_alertas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    alertas = obtener_alertas_usuario(chat_id)
    if not alertas:
        await update.message.reply_text("No tenés alertas activas. Usá /alerta para crear una.")
        return
    mensaje = "🔔 Tus alertas activas:\n\n"
    for a in alertas:
        mensaje += f"🚗 {a['marca']} {a['modelo']} {a['anio']}\n"
    await update.message.reply_text(mensaje)

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelado.")
    return ConversationHandler.END

async def buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¿Qué marca buscás? (Ej: Toyota, Volkswagen, Ford)")
    return MARCA

async def alerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("¿Qué marca querés monitorear? (Ej: Toyota, Volkswagen, Ford)")
    return ALERTA_MARCA

async def recibir_marca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['marca'] = update.message.text.strip()
    await update.message.reply_text("¿Qué modelo? (Ej: Corolla, Golf, Focus)")
    return MODELO

async def recibir_modelo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['modelo'] = update.message.text.strip()
    await update.message.reply_text("¿Año aproximado? (Ej: 2020, o escribí 'cualquiera')")
    return ANIO

async def recibir_anio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    anio = update.message.text.strip()
    marca = context.user_data['marca']
    modelo = context.user_data['modelo']

    if anio.lower() == 'cualquiera':
        query = f"{marca} {modelo}"
    else:
        context.user_data['anio'] = anio
        query = f"{marca} {modelo} {anio}"

    await update.message.reply_text(f"🔍 Buscando '{query}' en Mercado Libre, aguardá...")

    try:
        items_data = await scrapear_meli(query)
        if not items_data:
            await update.message.reply_text("No encontré resultados. Probá con otra búsqueda.")
            return ConversationHandler.END

        dolar_blue = obtener_dolar_blue()
        if dolar_blue:
            await update.message.reply_text(f"💵 Dólar blue: ${dolar_blue} ARS\n🤖 Claude está analizando las oportunidades...")
        else:
            await update.message.reply_text("🤖 Claude está analizando las oportunidades...")

        respuesta_claude = filtrar_y_analizar(query, items_data, dolar_blue)
        if not respuesta_claude:
            await update.message.reply_text("Error al analizar con Claude.")
            return ConversationHandler.END

        seleccionados = parsear_respuesta_claude(respuesta_claude, items_data)
        if not seleccionados:
            await update.message.reply_text("No se pudieron parsear los resultados.")
            return ConversationHandler.END

        mensaje = f"💼 Mejores oportunidades para '{query}':\n\n"
        for auto in seleccionados:
            try:
                precio_num = float(auto['precio'].replace('.', '').replace(',', '.'))
            except:
                precio_num = 0
            baja = detectar_baja_precio(auto['titulo'], precio_num, auto['link'])
            guardar_auto(auto['titulo'], precio_num, auto['link'], auto['anio'], auto['km'])
            semaforo = obtener_semaforo(auto['analisis'])
            alerta_baja = f"⚠️ BAJÓ DE PRECIO: antes ${baja['precio_anterior']:,.0f} → ahora ${precio_num:,.0f} (-{baja['porcentaje']:.1f}%)\n" if baja['bajo'] else ""
            mensaje += f"{semaforo} {auto['titulo']}\n📅 {auto['anio']} | 🛣️ {auto['km']}\n⚙️ {auto['motor']} | 🏷️ {auto['version']}\n💰 {auto['moneda']} {auto['precio']}\n"
            if alerta_baja:
                mensaje += alerta_baja
            mensaje += f"🔗 {auto['link']}\n📊 {auto['analisis']}\n\n"

        await update.message.reply_text(mensaje)
        await update.message.reply_text("✅ Oportunidades guardadas en la base de datos.")
    except Exception as e:
        print(f"ERROR: {e}")
        await update.message.reply_text(f"Error: {str(e)}")
    return ConversationHandler.END

async def recibir_alerta_marca(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['alerta_marca'] = update.message.text.strip()
    await update.message.reply_text("¿Qué modelo? (Ej: Corolla, Golf, Focus)")
    return ALERTA_MODELO

async def recibir_alerta_modelo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['alerta_modelo'] = update.message.text.strip()
    await update.message.reply_text("¿Año? (Ej: 2020, o escribí 'cualquiera')")
    return ALERTA_ANIO

async def recibir_alerta_anio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    anio = update.message.text.strip()
    marca = context.user_data['alerta_marca']
    modelo = context.user_data['alerta_modelo']
    chat_id = update.message.chat_id
    ok = guardar_alerta(chat_id, marca, modelo, anio)
    if ok:
        await update.message.reply_text(f"✅ Alerta creada para {marca} {modelo} {anio}.\n\nTe voy a avisar cada 6 horas si encuentro oportunidades 🟢 o bajas de precio ⚠️")
    else:
        await update.message.reply_text("Hubo un error guardando la alerta. Intentá de nuevo.")
    return ConversationHandler.END

async def post_init(app):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(job_alertas, 'interval', hours=6, args=[app])
    scheduler.start()
    print("Scheduler de alertas iniciado.")

def main():
    threading.Thread(target=run_server, daemon=True).start()
    print("Servidor HTTP iniciado en puerto 8080")

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    conv_buscar = ConversationHandler(
        entry_points=[CommandHandler("buscar", buscar)],
        states={
            MARCA: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_marca)],
            MODELO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_modelo)],
            ANIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_anio)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)]
    )

    conv_alerta = ConversationHandler(
        entry_points=[CommandHandler("alerta", alerta)],
        states={
            ALERTA_MARCA: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_alerta_marca)],
            ALERTA_MODELO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_alerta_modelo)],
            ALERTA_ANIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_alerta_anio)],
        },
        fallbacks=[CommandHandler("cancelar", cancelar)]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ayuda", ayuda))
    app.add_handler(CommandHandler("mis_alertas", mis_alertas))
    app.add_handler(conv_buscar)
    app.add_handler(conv_alerta)

    print("¡Bot AutoROI encendido y escuchando en Telegram!")
    app.run_polling()

if __name__ == '__main__':
    main()