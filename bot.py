import os
import sys
import logging
import traceback
import asyncio
import signal
import re
from datetime import datetime
from typing import Optional, Dict, Any

from telegram import Update, BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)
from telegram.error import TelegramError, NetworkError, TimedOut, BadRequest

# Configurações
BOT_TOKEN = "7897175834:AAH89XclhT7nuTUxQu_-mFh-y4h9U9vrI68"
ADMIN_ID = 5394278941

# Planos de vendas
PLANOS_CONFIG = {
    "1dia": {"nome": "1 dia", "valor": "R$1,90", "url": "https://mpago.la/1w9Ub5S"},
    "7dias": {"nome": "7 dias", "valor": "R$6,00", "url": "https://mpago.la/1Wo2Yof"},
    "1mes": {"nome": "1 mês", "valor": "R$16,00", "url": "https://mpago.la/1wm1afH"},
    "90dias": {"nome": "90 dias", "valor": "R$29,00", "url": "https://mpago.la/1vxTRn8"}
}

PASSE_ELITE_URL = "https://mpago.li/2zaGF45"

# Firebase
try:
    import firebase_admin
    from firebase_admin import credentials, db
    FIREBASE_AVAILABLE = True
except ImportError:
    FIREBASE_AVAILABLE = False

def initialize_firebase():
    """Inicializar Firebase com credenciais específicas"""
    if not FIREBASE_AVAILABLE:
        print("⚠️ Firebase não disponível")
        return False

    try:
        if firebase_admin._apps:
            print("✅ Firebase já inicializado")
            return True

        # Ajuste o caminho conforme necessário
        cred_path = "CREDENCIAIS.json"
        if not os.path.exists(cred_path):
            print(f"❌ Arquivo de credenciais não encontrado: {cred_path}")
            return False

        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred, {
            'databaseURL': 'https://luxzin-bot7-default-rtdb.firebaseio.com'
        })
        print("✅ Firebase conectado com sucesso")
        return True
    except Exception as e:
        print(f"❌ Erro ao conectar Firebase: {e}")
        return False

class LuxzinBotManager:
    """Gerenciador principal do Luxzin Bot com Firebase"""

    def __init__(self):
        self.application: Optional[Application] = None
        self.is_running = False
        self.restart_count = 0
        self.max_restarts = 5
        self.start_time = None
        self.firebase_connected = False

        # Armazenamento temporário se Firebase não disponível
        self.usuarios_temp = {}
        self.planos_gratis_temp = {}
        self.admins_temp = {str(ADMIN_ID): {"principal": True}}
        self.logs_temp = {}
        self.compras_temp = {}

        # Configurar logging
        self.setup_logging()
        self.logger = logging.getLogger(__name__)

    def setup_logging(self):
        """Configurar sistema de logging"""
        os.makedirs('logs', exist_ok=True)

        detailed_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )

        # Handler para arquivo
        file_handler = logging.FileHandler('logs/luxzin_bot.log', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)

        # Handler para erros
        error_handler = logging.FileHandler('logs/errors.log', encoding='utf-8')
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(detailed_formatter)

        # Handler para console
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))

        # Configurar logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        root_logger.handlers.clear()
        root_logger.addHandler(file_handler)
        root_logger.addHandler(error_handler)
        root_logger.addHandler(console_handler)

    async def initialize_bot(self) -> bool:
        """Inicializar bot"""
        try:
            self.logger.info("🚀 Iniciando Luxzin Bot...")

            # Tentar inicializar Firebase
            self.firebase_connected = initialize_firebase()
            if not self.firebase_connected:
                self.logger.warning("⚠️ Firebase não conectado - usando armazenamento temporário")

            if not BOT_TOKEN:
                self.logger.error("❌ Token do bot não encontrado")
                return False

            # Criar application
            self.application = Application.builder().token(BOT_TOKEN).build()

            # Configurar handlers
            self.setup_handlers()

            # Handler de erro global
            self.application.add_error_handler(self.global_error_handler)

            # Definir comandos
            await self.set_bot_commands()

            self.logger.info("✅ Bot inicializado com sucesso")
            return True

        except Exception as e:
            self.logger.error(f"❌ Erro na inicialização: {str(e)}")
            return False

    def setup_handlers(self):
        """Configurar todos os handlers"""
        # Comandos principais
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("ajuda", self.help_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("id", self.id_command))

        # Comandos de vendas
        self.application.add_handler(CommandHandler("gratis", self.free_plan_command))
        self.application.add_handler(CommandHandler("bot", self.bot_plans_command))
        self.application.add_handler(CommandHandler("passe", self.elite_pass_command))

        # Comandos administrativos
        self.application.add_handler(CommandHandler("addadmin", self.add_admin_command))
        self.application.add_handler(CommandHandler("removeradmin", self.remove_admin_command))
        self.application.add_handler(CommandHandler("logs", self.logs_command))
        self.application.add_handler(CommandHandler("stats", self.stats_command))
        self.application.add_handler(CommandHandler("avisos", self.broadcast_command))

        # Comandos de sistema
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("debug", self.debug_command))
        self.application.add_handler(CommandHandler("reiniciar", self.restart_command))

        # Callbacks
        self.application.add_handler(CallbackQueryHandler(self.free_plan_callback, pattern="^free_plan_callback$"))
        self.application.add_handler(CallbackQueryHandler(self.view_paid_plans_callback, pattern="^view_paid_plans$"))
        self.application.add_handler(CallbackQueryHandler(self.buy_plan_callback, pattern="^buy_"))
        self.application.add_handler(CallbackQueryHandler(self.confirm_delivery_callback, pattern="^confirm_delivery_"))
        self.application.add_handler(CallbackQueryHandler(self.confirm_payment_callback, pattern="^confirm_payment_"))

        # Handler de mensagens de texto
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))

    async def set_bot_commands(self):
        """Definir comandos do bot"""
        commands = [
            BotCommand("start", "Iniciar o bot"),
            BotCommand("ajuda", "Mostrar comandos disponíveis"),
            BotCommand("gratis", "Ativar plano grátis (1 hora)"),
            BotCommand("bot", "Ver planos do Bot de Likes"),
            BotCommand("passe", "Comprar Passe de Elite"),
            BotCommand("id", "Ver seu ID do Telegram"),
        ]

        try:
            await self.application.bot.set_my_commands(commands)
            self.logger.info("✅ Comandos definidos")
        except Exception as e:
            self.logger.warning(f"⚠️ Erro ao definir comandos: {str(e)}")

    # === UTILITÁRIOS ===
    def validar_id_freefire(self, id_text: str) -> bool:
        """Validar ID do Free Fire"""
        if not id_text:
            return False
        id_clean = re.sub(r'[^\d]', '', id_text)
        return len(id_clean) >= 8 and id_clean.isdigit()

    async def is_admin(self, user_id: int) -> bool:
        """Verificar se usuário é admin"""
        try:
            if user_id == ADMIN_ID:
                return True

            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference("admins")
                admins = ref.get() or {}
                return str(user_id) in admins
            else:
                return str(user_id) in self.admins_temp
        except Exception as e:
            self.logger.error(f"Erro ao verificar admin {user_id}: {e}")
            return user_id == ADMIN_ID

    async def save_user(self, user_id: int, username: str, chat_id: int, chat_type: str):
        """Salvar dados do usuário"""
        try:
            user_data = {
                "username": username or "sem username",
                "chat_id": chat_id,
                "chat_type": chat_type,
                "ultima_interacao": str(datetime.now())
            }

            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference("usuarios")
                ref.update({str(user_id): user_data})
            else:
                self.usuarios_temp[str(user_id)] = user_data

            self.logger.debug(f"Usuário {user_id} salvo")
        except Exception as e:
            self.logger.error(f"Erro ao salvar usuário {user_id}: {e}")

    async def has_used_free_plan(self, user_id: int) -> bool:
        """Verificar se já usou plano grátis"""
        try:
            if await self.is_admin(user_id):
                return False

            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference(f"planos_gratis/{user_id}")
                dados = ref.get()
                return dados is not None
            else:
                return str(user_id) in self.planos_gratis_temp
        except Exception as e:
            self.logger.error(f"Erro ao verificar plano grátis para {user_id}: {e}")
            return False

    async def register_free_plan(self, user_id: int, username: str, freefire_id: str) -> bool:
        """Registrar uso do plano grátis"""
        try:
            is_admin = await self.is_admin(user_id)

            dados = {
                "username": username or "sem username",
                "freefire_id": freefire_id,
                "data_uso": str(datetime.now()),
                "status": "ativo",
                "is_admin": is_admin
            }

            if not is_admin:
                if self.firebase_connected and FIREBASE_AVAILABLE:
                    ref = db.reference(f"planos_gratis/{user_id}")
                    ref.set(dados)
                else:
                    self.planos_gratis_temp[str(user_id)] = dados

            await self.log_transaction("plano_gratis", user_id, username, freefire_id,
                                     "Admin usou plano grátis ilimitado" if is_admin else "Plano grátis ativado")

            await self.notify_admin_activity(user_id, username, freefire_id,
                                           "gratis_admin" if is_admin else "gratis")
            return True
        except Exception as e:
            self.logger.error(f"Erro ao registrar plano grátis para {user_id}: {e}")
            return False

    async def log_transaction(self, tipo: str, user_id: int, username: str, freefire_id: str, detalhes: str):
        """Registrar log de transação"""
        try:
            timestamp = str(datetime.now())
            log_id = f"{user_id}_{int(datetime.now().timestamp())}"

            log_data = {
                "tipo": tipo,
                "user_id": user_id,
                "username": username or "sem username",
                "freefire_id": freefire_id,
                "detalhes": detalhes,
                "timestamp": timestamp,
                "status": "pendente"
            }

            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference("logs_transacoes")
                ref.child(log_id).set(log_data)
            else:
                self.logs_temp[log_id] = log_data

            self.logger.info(f"Log registrado: {tipo} - {user_id} - {detalhes}")
        except Exception as e:
            self.logger.error(f"Erro ao registrar log: {e}")

    async def notify_admin_activity(self, user_id: int, username: str, freefire_id: str, tipo_plano: str):
        """Notificar admin sobre atividade"""
        try:
            is_admin = await self.is_admin(user_id)

            if is_admin:
                mensagem = (
                    f"👑 ADMIN USOU PLANO GRÁTIS!\n\n"
                    f"👤 Admin: @{username or 'sem username'}\n"
                    f"🎮 ID Free Fire: {freefire_id}\n"
                    f"📦 Tipo: {tipo_plano.upper()}\n"
                    f"⏰ Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
                    f"✅ Processamento automático para admin\n\n"
                    f"Em caso de dúvidas: @Luxzin7"
                )
                reply_markup = None
            else:
                mensagem = (
                    f"🔔 NOVA ATIVIDADE NO BOT!\n\n"
                    f"👤 Usuário: @{username or 'sem username'} (ID: {user_id})\n"
                    f"🎮 ID Free Fire: {freefire_id}\n"
                    f"📦 Tipo: {tipo_plano.upper()}\n"
                    f"⏰ Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
                    f"Use o botão abaixo para confirmar entrega\n\n"
                    f"Em caso de dúvidas: @Luxzin7"
                )
                keyboard = [
                    [InlineKeyboardButton("✅ Confirmar Entrega",
                                        callback_data=f"confirm_delivery_{user_id}_{int(datetime.now().timestamp())}")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(chat_id=ADMIN_ID, text=mensagem, reply_markup=reply_markup)
        except Exception as e:
            self.logger.error(f"Erro ao notificar admin: {e}")

    # === COMANDOS PRINCIPAIS ===
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /start"""
        try:
            user = update.message.from_user
            await self.save_user(user.id, user.username, update.effective_chat.id, update.effective_chat.type)

            welcome_message = (
                f"👋 Bem-vindo ao **Luxzin Bot**, {user.first_name}!\n\n"
                f"🎮 **Serviços disponíveis:**\n"
                f"🆓 /gratis - Plano grátis de 1 hora\n"
                f"🤖 /bot - Bot de Likes (vários planos)\n"
                f"🎟️ /passe - Passe de Elite\n\n"
                f"ℹ️ /ajuda - Ver todos os comandos\n\n"
                f"Em caso de dúvidas: @Luxzin7"
            )

            await update.message.reply_text(welcome_message, parse_mode='Markdown')
            self.logger.info(f"Comando /start executado por {user.id}")

        except Exception as e:
            self.logger.error(f"Erro no comando /start: {e}")
            await update.message.reply_text("❌ Erro inesperado. Tente novamente.")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /ajuda"""
        try:
            user = update.message.from_user
            is_admin = await self.is_admin(user.id)

            help_text = (
                "🔧 **LUXZIN BOT - COMANDOS**\n\n"
                "**👤 Comandos para Usuários:**\n"
                "/start - Iniciar o bot\n"
                "/gratis - Ativar plano grátis (1 hora)\n"
                "/bot - Ver planos do Bot de Likes\n"
                "/passe - Comprar Passe de Elite\n"
                "/id - Ver seu ID do Telegram\n\n"
            )

            if is_admin:
                help_text += (
                    "**👑 Comandos Administrativos:**\n"
                    "/addadmin <ID> - Adicionar administrador\n"
                    "/removeradmin <ID> - Remover administrador\n"
                    "/logs - Ver logs de transações\n"
                    "/stats - Ver estatísticas do bot\n"
                    "/avisos <mensagem> - Enviar aviso para todos\n\n"
                )

            help_text += (
                "**📦 Planos Disponíveis:**\n"
                "🆓 Grátis - 1 hora (uso único)\n"
                "⚡ 1 dia - R$1,90\n"
                "📅 7 dias - R$6,00\n"
                "📆 1 mês - R$16,00\n"
                "🔥 90 dias - R$29,00\n\n"
                "Em caso de dúvidas: @Luxzin7"
            )

            await update.message.reply_text(help_text, parse_mode='Markdown')

        except Exception as e:
            self.logger.error(f"Erro no comando /ajuda: {e}")
            await update.message.reply_text("❌ Erro inesperado. Tente novamente.")

    async def id_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /id"""
        try:
            user = update.message.from_user
            await update.message.reply_text(
                f"🆔 **Suas informações:**\n\n"
                f"**ID:** `{user.id}`\n"
                f"**Nome:** {user.full_name or 'N/A'}\n"
                f"**Username:** @{user.username or 'sem username'}\n\n"
                f"Em caso de dúvidas: @Luxzin7",
                parse_mode='Markdown'
            )
        except Exception as e:
            self.logger.error(f"Erro no comando /id: {e}")
            await update.message.reply_text("❌ Erro inesperado. Tente novamente.")

    # === COMANDOS DE VENDAS ===
    async def free_plan_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /gratis"""
        try:
            user = update.message.from_user
            await self.save_user(user.id, user.username, update.effective_chat.id, update.effective_chat.type)

            is_admin = await self.is_admin(user.id)

            if is_admin:
                await update.message.reply_text(
                    "👑 **ADMIN DETECTADO**\n\n"
                    "🎮 Digite seu ID do Free Fire para ativar o plano grátis ilimitado:\n\n"
                    "Em caso de dúvidas: @Luxzin7"
                )
                context.user_data['awaiting_freefire_id'] = 'admin_free'
                return

            has_used = await self.has_used_free_plan(user.id)
            if has_used:
                keyboard = [
                    [InlineKeyboardButton("🛒 Ver Planos Pagos", callback_data="view_paid_plans")]
                ]
                await update.message.reply_text(
                    "❌ Você já usou seu plano grátis de 1 hora!\n\n"
                    "📦 Confira nossos planos pagos para continuar:\n\n"
                    "Em caso de dúvidas: @Luxzin7",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            await update.message.reply_text(
                "🎮 **PLANO GRÁTIS - 1 HORA**\n\n"
                "Para ativar seu plano grátis, digite seu ID do Free Fire:\n"
                "*(mínimo 8 dígitos)*\n\n"
                "Em caso de dúvidas: @Luxzin7"
            )
            context.user_data['awaiting_freefire_id'] = 'free_plan'

        except Exception as e:
            self.logger.error(f"Erro no comando /gratis: {e}")
            await update.message.reply_text("❌ Erro inesperado. Tente novamente.")

    async def bot_plans_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /bot"""
        try:
            user = update.message.from_user
            await self.save_user(user.id, user.username, update.effective_chat.id, update.effective_chat.type)

            keyboard = [
                [InlineKeyboardButton("🆓 Grátis (1 hora)", callback_data="free_plan_callback")],
                [InlineKeyboardButton("⚡ 1 dia - R$1,90", callback_data="buy_1dia")],
                [InlineKeyboardButton("📅 7 dias - R$6,00", callback_data="buy_7dias")],
                [InlineKeyboardButton("📆 1 mês - R$16,00", callback_data="buy_1mes")],
                [InlineKeyboardButton("🔥 90 dias - R$29,00", callback_data="buy_90dias")]
            ]

            plans_text = (
                "🤖 **BOT DE LIKES - PLANOS DISPONÍVEIS**\n\n"
                "🆓 **Grátis** - 1 hora (teste gratuito)\n"
                "⚡ **1 dia** - R$1,90\n"
                "📅 **7 dias** - R$6,00\n"
                "📆 **1 mês** - R$16,00\n"
                "🔥 **90 dias** - R$29,00\n\n"
                "💳 Selecione o plano desejado:\n\n"
                "Em caso de dúvidas: @Luxzin7"
            )

            await update.message.reply_text(
                plans_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )

        except Exception as e:
            self.logger.error(f"Erro no comando /bot: {e}")
            await update.message.reply_text("❌ Erro inesperado. Tente novamente.")

    async def elite_pass_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /passe"""
        try:
            user = update.message.from_user
            await self.save_user(user.id, user.username, update.effective_chat.id, update.effective_chat.type)

            await update.message.reply_text(
                "🎟️ **PASSE DE ELITE DISPONÍVEL**\n\n"
                "✨ Para adquirir o Passe de Elite, digite seu ID do Free Fire:\n"
                "*(mínimo 8 dígitos)*\n\n"
                "Em caso de dúvidas: @Luxzin7"
            )
            context.user_data['awaiting_freefire_id'] = 'elite_pass'

        except Exception as e:
            self.logger.error(f"Erro no comando /passe: {e}")
            await update.message.reply_text("❌ Erro inesperado. Tente novamente.")

    # === CALLBACKS ===
    async def free_plan_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Callback plano grátis"""
        query = update.callback_query
        await query.answer()

        try:
            user = query.from_user
            is_admin = await self.is_admin(user.id)

            if is_admin:
                await query.edit_message_text(
                    "👑 **ADMIN DETECTADO**\n\n"
                    "🎮 Digite seu ID do Free Fire para ativar o plano grátis ilimitado:\n\n"
                    "Em caso de dúvidas: @Luxzin7"
                )
                context.user_data['awaiting_freefire_id'] = 'admin_free'
                return

            has_used = await self.has_used_free_plan(user.id)
            if has_used:
                keyboard = [
                    [InlineKeyboardButton("🛒 Ver Planos Pagos", callback_data="view_paid_plans")]
                ]
                await query.edit_message_text(
                    "❌ Você já usou seu plano grátis de 1 hora!\n\n"
                    "📦 Confira nossos planos pagos para continuar:\n\n"
                    "Em caso de dúvidas: @Luxzin7",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            await query.edit_message_text(
                "🎮 **PLANO GRÁTIS - 1 HORA**\n\n"
                "Para ativar seu plano grátis, digite seu ID do Free Fire:\n"
                "*(mínimo 8 dígitos)*\n\n"
                "Em caso de dúvidas: @Luxzin7"
            )
            context.user_data['awaiting_freefire_id'] = 'free_plan'

        except Exception as e:
            self.logger.error(f"Erro no callback plano grátis: {e}")
            await query.edit_message_text("❌ Erro inesperado. Tente novamente.")

    async def view_paid_plans_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Callback ver planos pagos"""
        query = update.callback_query
        await query.answer()

        try:
            keyboard = [
                [InlineKeyboardButton("⚡ 1 dia - R$1,90", callback_data="buy_1dia")],
                [InlineKeyboardButton("📅 7 dias - R$6,00", callback_data="buy_7dias")],
                [InlineKeyboardButton("📆 1 mês - R$16,00", callback_data="buy_1mes")],
                [InlineKeyboardButton("🔥 90 dias - R$29,00", callback_data="buy_90dias")]
            ]

            plans_text = (
                "🤖 **BOT DE LIKES - PLANOS PAGOS**\n\n"
                "⚡ **1 dia** - R$1,90\n"
                "📅 **7 dias** - R$6,00\n"
                "📆 **1 mês** - R$16,00\n"
                "🔥 **90 dias** - R$29,00\n\n"
                "💳 Selecione o plano desejado:\n\n"
                "Em caso de dúvidas: @Luxzin7"
            )

            await query.edit_message_text(
                plans_text,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

        except Exception as e:
            self.logger.error(f"Erro no callback planos pagos: {e}")
            await query.edit_message_text("❌ Erro inesperado. Tente novamente.")

    async def buy_plan_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Callback compra de planos"""
        query = update.callback_query
        await query.answer()

        try:
            plan_type = query.data.replace("buy_", "")

            if plan_type not in PLANOS_CONFIG:
                await query.edit_message_text("❌ Plano não encontrado.")
                return

            plan_info = PLANOS_CONFIG[plan_type]

            await query.edit_message_text(
                f"📦 **Plano selecionado:** {plan_info['nome']} - {plan_info['valor']}\n\n"
                f"🎮 Digite seu ID do Free Fire para continuar:\n"
                f"*(mínimo 8 dígitos)*\n\n"
                f"Em caso de dúvidas: @Luxzin7"
            )

            context.user_data['awaiting_freefire_id'] = f'buy_{plan_type}'
            context.user_data['selected_plan'] = plan_info

        except Exception as e:
            self.logger.error(f"Erro no callback compra: {e}")
            await query.edit_message_text("❌ Erro inesperado. Tente novamente.")

    async def confirm_delivery_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Callback confirmar entrega"""
        query = update.callback_query
        await query.answer()

        if not await self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Acesso negado.")
            return

        try:
            callback_data = query.data
            parts = callback_data.split('_')
            if len(parts) >= 3 and parts[0] == "confirm" and parts[1] == "delivery":
                target_user_id = int(parts[2])

                # Atualizar status nos logs
                if self.firebase_connected and FIREBASE_AVAILABLE:
                    ref = db.reference("logs_transacoes")
                    logs = ref.get() or {}
                    for log_id, log_data in logs.items():
                        if (str(log_data.get('user_id')) == str(target_user_id) and
                            log_data.get('status') == 'pendente'):
                            ref.child(log_id).update({"status": "entregue", "confirmado_em": str(datetime.now())})
                            break
                else:
                    for log_id, log_data in self.logs_temp.items():
                        if (str(log_data.get('user_id')) == str(target_user_id) and
                            log_data.get('status') == 'pendente'):
                            self.logs_temp[log_id]['status'] = 'entregue'
                            self.logs_temp[log_id]['confirmado_em'] = str(datetime.now())
                            break

                # Notificar usuário
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=(
                            f"✅ PEDIDO CONFIRMADO!\n\n"
                            f"Seu pedido foi processado com sucesso!\n"
                            f"🎮 Verifique se há uma solicitação de amizade pendente no Free Fire.\n\n"
                            f"Em caso de dúvidas: @Luxzin7"
                        )
                    )
                except Exception as e:
                    self.logger.error(f"Erro ao notificar usuário {target_user_id}: {e}")

                await query.edit_message_text(
                    f"✅ Entrega confirmada para usuário {target_user_id}!\n"
                    f"🕒 Confirmado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
                )

        except Exception as e:
            await query.edit_message_text(f"❌ Erro ao confirmar entrega: {e}")

    async def confirm_payment_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Callback confirmar pagamento"""
        query = update.callback_query
        await query.answer()

        if not await self.is_admin(query.from_user.id):
            await query.edit_message_text("❌ Acesso negado.")
            return

        try:
            callback_data = query.data
            compra_id = callback_data.replace("confirm_payment_", "")

            # Buscar informações da compra
            compra_info = None
            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference(f"aguardando_pagamento/{compra_id}")
                compra_info = ref.get()
                if compra_info:
                    ref.delete()
            else:
                compra_info = self.compras_temp.get(compra_id)
                if compra_info:
                    del self.compras_temp[compra_id]

            if compra_info:
                # Notificar usuário
                try:
                    await context.bot.send_message(
                        chat_id=compra_info["user_id"],
                        text=(
                            f"✅ PAGAMENTO CONFIRMADO!\n\n"
                            f"🎮 Plano: {compra_info['plano_tipo']}\n"
                            f"🆔 ID Free Fire: {compra_info['freefire_id']}\n"
                            f"⏰ Ativado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
                            f"Verifique se há uma solicitação de amizade pendente no Free Fire.\n\n"
                            f"Em caso de dúvidas: @Luxzin7"
                        )
                    )
                except Exception as e:
                    self.logger.error(f"Erro ao notificar usuário: {e}")

                await query.edit_message_text(
                    f"✅ Pagamento confirmado!\n"
                    f"📦 Plano: {compra_info['plano_tipo']}\n"
                    f"👤 Usuário: @{compra_info['username']}\n"
                    f"🕒 Confirmado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
                )
            else:
                await query.edit_message_text("❌ Informações da compra não encontradas.")

        except Exception as e:
            await query.edit_message_text(f"❌ Erro ao confirmar pagamento: {e}")

    # === COMANDOS ADMINISTRATIVOS ===
    async def add_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /addadmin"""
        if update.message.from_user.id != ADMIN_ID:
            await update.message.reply_text("❌ Apenas o administrador principal pode adicionar admins.")
            return

        if not context.args:
            await update.message.reply_text("💬 Use: /addadmin <ID_do_usuário>")
            return

        try:
            new_admin_id = int(context.args[0])
            admin_data = {
                "adicionado_por": ADMIN_ID,
                "data_adicao": str(datetime.now()),
                "principal": False
            }

            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference("admins")
                ref.update({str(new_admin_id): admin_data})
            else:
                self.admins_temp[str(new_admin_id)] = admin_data

            await update.message.reply_text(f"✅ Admin {new_admin_id} adicionado com sucesso!")
        except ValueError:
            await update.message.reply_text("❌ ID inválido. Use apenas números.")
        except Exception as e:
            self.logger.error(f"Erro ao adicionar admin: {e}")
            await update.message.reply_text("❌ Erro ao adicionar admin.")

    async def remove_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /removeradmin"""
        if update.message.from_user.id != ADMIN_ID:
            await update.message.reply_text("❌ Apenas o administrador principal pode remover admins.")
            return

        if not context.args:
            await update.message.reply_text("💬 Use: /removeradmin <ID_do_usuário>")
            return

        try:
            admin_id = int(context.args[0])
            if admin_id == ADMIN_ID:
                await update.message.reply_text("❌ Não é possível remover o administrador principal.")
                return

            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference(f"admins/{admin_id}")
                ref.delete()
            else:
                if str(admin_id) in self.admins_temp:
                    del self.admins_temp[str(admin_id)]

            await update.message.reply_text(f"✅ Admin {admin_id} removido com sucesso!")
        except ValueError:
            await update.message.reply_text("❌ ID inválido. Use apenas números.")
        except Exception as e:
            self.logger.error(f"Erro ao remover admin: {e}")
            await update.message.reply_text("❌ Erro ao remover admin.")

    async def logs_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /logs"""
        if not await self.is_admin(update.message.from_user.id):
            await update.message.reply_text("❌ Comando disponível apenas para administradores.")
            return

        try:
            logs = {}
            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference("logs_transacoes")
                logs = ref.get() or {}
            else:
                logs = self.logs_temp

            if not logs:
                await update.message.reply_text("📄 Nenhum log encontrado.")
                return

            logs_ordenados = sorted(logs.items(), key=lambda x: x[1].get('timestamp', ''), reverse=True)[:10]

            mensagem = "📊 ÚLTIMOS LOGS (10 mais recentes):\n\n"
            for log_id, log_data in logs_ordenados:
                status_emoji = "✅" if log_data.get('status') == 'entregue' else "⏳"
                mensagem += (
                    f"{status_emoji} {log_data.get('tipo', 'N/A').upper()}\n"
                    f"👤 @{log_data.get('username', 'N/A')} (ID: {log_data.get('user_id', 'N/A')})\n"
                    f"🎮 FF ID: {log_data.get('freefire_id', 'N/A')}\n"
                    f"📅 {log_data.get('timestamp', 'N/A')[:19]}\n\n"
                )

            await update.message.reply_text(mensagem[:4000])

        except Exception as e:
            self.logger.error(f"Erro nos logs: {e}")
            await update.message.reply_text("❌ Erro ao buscar logs.")

    async def stats_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /stats"""
        if not await self.is_admin(update.message.from_user.id):
            await update.message.reply_text("❌ Comando disponível apenas para administradores.")
            return

        try:
            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref_usuarios = db.reference("usuarios")
                usuarios = ref_usuarios.get() or {}
                ref_gratis = db.reference("planos_gratis")
                planos_gratis = ref_gratis.get() or {}
                ref_logs = db.reference("logs_transacoes")
                logs = ref_logs.get() or {}
                ref_compras = db.reference("aguardando_pagamento")
                compras_pendentes = ref_compras.get() or {}
            else:
                usuarios = self.usuarios_temp
                planos_gratis = self.planos_gratis_temp
                logs = self.logs_temp
                compras_pendentes = self.compras_temp

            total_usuarios = len(usuarios)
            total_gratis = len(planos_gratis)
            total_transacoes = len(logs)
            total_compras_pendentes = len(compras_pendentes)

            entregues = sum(1 for log in logs.values() if log.get('status') == 'entregue')
            pendentes = total_transacoes - entregues

            storage_type = "Firebase" if (self.firebase_connected and FIREBASE_AVAILABLE) else "Temporário"

            mensagem = (
                f"📊 ESTATÍSTICAS DO BOT\n\n"
                f"👥 Total de usuários: {total_usuarios}\n"
                f"🆓 Planos grátis usados: {total_gratis}\n"
                f"📝 Total de transações: {total_transacoes}\n"
                f"✅ Entregas confirmadas: {entregues}\n"
                f"⏳ Pendentes: {pendentes}\n"
                f"💰 Compras aguardando: {total_compras_pendentes}\n\n"
                f"💾 Armazenamento: {storage_type}\n"
                f"📅 Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
            )

            await update.message.reply_text(mensagem)

        except Exception as e:
            self.logger.error(f"Erro nas estatísticas: {e}")
            await update.message.reply_text("❌ Erro ao buscar estatísticas.")

    async def broadcast_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /avisos"""
        if not await self.is_admin(update.message.from_user.id):
            await update.message.reply_text("❌ Comando disponível apenas para administradores.")
            return

        if not context.args:
            await update.message.reply_text("💬 Use: /avisos <mensagem>")
            return

        mensagem = " ".join(context.args)
        try:
            usuarios = {}
            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference("usuarios")
                usuarios = ref.get() or {}
            else:
                usuarios = self.usuarios_temp

            enviados, erros = 0, 0

            for user_id_str, dados in usuarios.items():
                try:
                    chat_id = dados.get("chat_id")
                    if chat_id:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=f"📢 AVISO DO LUXZIN BOT:\n\n{mensagem}\n\nEm caso de dúvidas: @Luxzin7"
                        )
                        enviados += 1
                except Exception as e:
                    erros += 1
                    self.logger.error(f"Erro ao enviar para {user_id_str}: {e}")

            await update.message.reply_text(f"✅ Aviso enviado!\n📤 Enviados: {enviados}\n❌ Erros: {erros}")
        except Exception as e:
            self.logger.error(f"Erro nos avisos: {e}")
            await update.message.reply_text("❌ Erro ao enviar avisos.")

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /status"""
        try:
            storage_status = "Firebase ✅" if (self.firebase_connected and FIREBASE_AVAILABLE) else "Temporário ⚠️"

            status_text = (
                f"📊 **RELATÓRIO DE STATUS**\n\n"
                f"🔴 **Executando:** {'✅ Sim' if self.is_running else '❌ Não'}\n"
                f"🔄 **Reinicializações:** {self.restart_count}\n"
                f"⏱️ **Tempo Ativo:** {self.get_uptime()}\n"
                f"💾 **Armazenamento:** {storage_status}\n\n"
                f"🕒 **Última Verificação:** {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
            )

            await update.message.reply_text(status_text, parse_mode='Markdown')

        except Exception as e:
            self.logger.error(f"Erro no comando /status: {e}")
            await update.message.reply_text("❌ Erro ao verificar status.")

    async def debug_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /debug"""
        try:
            if not await self.is_admin(update.message.from_user.id):
                await update.message.reply_text("❌ Comando disponível apenas para administradores.")
                return

            firebase_status = "✅ Conectado" if (self.firebase_connected and FIREBASE_AVAILABLE) else "❌ Desconectado"

            debug_info = (
                f"🔍 **INFORMAÇÕES DE DEBUG**\n\n"
                f"**Token do Bot:** {'✅ Presente' if BOT_TOKEN else '❌ Ausente'}\n"
                f"**Aplicação:** {'✅ OK' if self.application else '❌ Falhou'}\n"
                f"**Firebase:** {firebase_status}\n"
                f"**Admin ID:** `{ADMIN_ID}`\n\n"
                f"**Horário do Debug:** {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
            )

            await update.message.reply_text(debug_info, parse_mode='Markdown')

        except Exception as e:
            self.logger.error(f"Erro no comando /debug: {e}")
            await update.message.reply_text("❌ Erro ao gerar debug.")

    async def restart_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /reiniciar"""
        try:
            user_id = update.effective_user.id

            if not await self.is_admin(user_id):
                await update.message.reply_text("❌ Acesso negado. Privilégios de admin necessários.")
                return

            await update.message.reply_text("🔄 Reiniciando bot...")
            asyncio.create_task(self.schedule_restart())

        except Exception as e:
            self.logger.error(f"Erro no comando /reiniciar: {e}")
            await update.message.reply_text("❌ Erro ao reiniciar.")

    # === HANDLER DE MENSAGENS DE TEXTO ===
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler para mensagens de texto (IDs do Free Fire)"""
        try:
            user = update.message.from_user
            await self.save_user(user.id, user.username, update.effective_chat.id, update.effective_chat.type)

            freefire_id = update.message.text.strip()
            awaiting_type = context.user_data.get('awaiting_freefire_id')

            if not awaiting_type:
                # Mensagem de eco
                response = (
                    "✅ Mensagem recebida!\n\n"
                    "Para ver os comandos disponíveis, digite /ajuda\n\n"
                    "Em caso de dúvidas: @Luxzin7"
                )
                await update.message.reply_text(response)
                return

            # Validar ID do Free Fire
            if not self.validar_id_freefire(freefire_id):
                await update.message.reply_text(
                    "❌ ID inválido! O ID do Free Fire deve ter pelo menos 8 dígitos numéricos.\n"
                    "Por favor, digite novamente:\n\nEm caso de dúvidas: @Luxzin7"
                )
                return

            # Processar conforme o tipo
            if awaiting_type in ['free_plan', 'admin_free']:
                await self.process_free_plan(update, context, freefire_id, awaiting_type == 'admin_free')
            elif awaiting_type == 'elite_pass':
                await self.process_elite_pass(update, context, freefire_id)
            elif awaiting_type.startswith('buy_'):
                await self.process_paid_plan(update, context, freefire_id, awaiting_type)

            # Limpar estado
            context.user_data['awaiting_freefire_id'] = None

        except Exception as e:
            self.logger.error(f"Erro ao processar mensagem de texto: {e}")
            await update.message.reply_text("❌ Erro ao processar mensagem. Tente novamente.")

    async def process_free_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE, freefire_id: str, is_admin: bool):
        """Processar plano grátis"""
        try:
            user = update.message.from_user
            success = await self.register_free_plan(user.id, user.username, freefire_id)

            if success:
                if is_admin:
                    await update.message.reply_text(
                        f"👑 PLANO GRÁTIS ILIMITADO ATIVADO!\n\n"
                        f"✅ ID Free Fire: {freefire_id}\n"
                        f"⚡ Status: Ativo (ilimitado para admin)\n"
                        f"🎮 Aguarde o processamento automático!\n\n"
                        f"Em caso de dúvidas: @Luxzin7"
                    )
                else:
                    await update.message.reply_text(
                        f"✅ PLANO GRÁTIS ATIVADO!\n\n"
                        f"🎮 ID Free Fire: {freefire_id}\n"
                        f"⏰ Duração: 1 hora\n"
                        f"🔥 Status: Ativo\n\n"
                        f"Você receberá uma notificação quando o pedido for processado!\n\n"
                        f"Em caso de dúvidas: @Luxzin7"
                    )
            else:
                await update.message.reply_text(
                    "❌ Erro ao ativar o plano grátis. Tente novamente mais tarde.\n\nEm caso de dúvidas: @Luxzin7"
                )

        except Exception as e:
            self.logger.error(f"Erro ao processar plano grátis: {e}")
            await update.message.reply_text("❌ Erro ao processar plano grátis.")

    async def process_elite_pass(self, update: Update, context: ContextTypes.DEFAULT_TYPE, freefire_id: str):
        """Processar compra do Passe de Elite"""
        try:
            user = update.message.from_user

            # Gerar ID da compra
            compra_id = f"passe_{user.id}_{int(datetime.now().timestamp())}"
            compra_data = {
                "user_id": user.id,
                "username": user.username or "sem username",
                "freefire_id": freefire_id,
                "plano_tipo": "Passe de Elite",
                "valor": "Consultar preço",
                "timestamp": str(datetime.now())
            }

            # Salvar compra pendente
            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference(f"aguardando_pagamento/{compra_id}")
                ref.set(compra_data)
            else:
                self.compras_temp[compra_id] = compra_data

            # Notificar admin
            mensagem_admin = (
                f"🎟️ NOVA COMPRA DE PASSE!\n\n"
                f"👤 Usuário: @{user.username or 'sem username'} (ID: {user.id})\n"
                f"🎮 ID Free Fire: {freefire_id}\n"
                f"📦 Produto: Passe de Elite\n"
                f"⏰ Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
                f"🔍 ID da Compra: {compra_id}\n\n"
                f"Use o botão abaixo para confirmar o pagamento\n\n"
                f"Em caso de dúvidas: @Luxzin7"
            )

            keyboard = [
                [InlineKeyboardButton("✅ Confirmar Pagamento",
                                    callback_data=f"confirm_payment_{compra_id}")]
            ]

            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=mensagem_admin,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # Enviar link de pagamento para o usuário
            keyboard_user = [
                [InlineKeyboardButton("💳 Realizar Pagamento", url=PASSE_ELITE_URL)]
            ]

            await update.message.reply_text(
                f"✅ ID Free Fire registrado: {freefire_id}\n\n"
                f"🎟️ Produto: Passe de Elite\n\n"
                f"💳 Clique no botão abaixo para realizar o pagamento.\n"
                f"Após o pagamento, você receberá a confirmação automaticamente!\n\n"
                f"Em caso de dúvidas: @Luxzin7",
                reply_markup=InlineKeyboardMarkup(keyboard_user)
            )

        except Exception as e:
            self.logger.error(f"Erro ao processar passe de elite: {e}")
            await update.message.reply_text("❌ Erro ao processar compra do passe.")

    async def process_paid_plan(self, update: Update, context: ContextTypes.DEFAULT_TYPE, freefire_id: str, awaiting_type: str):
        """Processar compra de plano pago"""
        try:
            user = update.message.from_user
            plan_type = awaiting_type.replace('buy_', '')
            plan_info = context.user_data.get('selected_plan', PLANOS_CONFIG.get(plan_type, {}))

            # Gerar ID da compra
            compra_id = f"{user.id}_{int(datetime.now().timestamp())}"
            compra_data = {
                "user_id": user.id,
                "username": user.username or "sem username",
                "freefire_id": freefire_id,
                "plano_tipo": plan_info['nome'],
                "valor": plan_info['valor'],
                "timestamp": str(datetime.now())
            }

            # Salvar compra pendente
            if self.firebase_connected and FIREBASE_AVAILABLE:
                ref = db.reference(f"aguardando_pagamento/{compra_id}")
                ref.set(compra_data)
            else:
                self.compras_temp[compra_id] = compra_data

            # Notificar admin
            mensagem_admin = (
                f"💰 NOVA COMPRA DE PLANO!\n\n"
                f"👤 Usuário: @{user.username or 'sem username'} (ID: {user.id})\n"
                f"🎮 ID Free Fire: {freefire_id}\n"
                f"📦 Plano: {plan_info['nome']}\n"
                f"💵 Valor: {plan_info['valor']}\n"
                f"⏰ Data: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n\n"
                f"🔍 ID da Compra: {compra_id}\n\n"
                f"Use o botão abaixo para confirmar o pagamento\n\n"
                f"Em caso de dúvidas: @Luxzin7"
            )

            keyboard = [
                [InlineKeyboardButton("✅ Confirmar Pagamento",
                                    callback_data=f"confirm_payment_{compra_id}")]
            ]

            bot = Bot(token=BOT_TOKEN)
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=mensagem_admin,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # Enviar link de pagamento para o usuário
            keyboard_user = [
                [InlineKeyboardButton("💳 Realizar Pagamento", url=plan_info['url'])]
            ]

            await update.message.reply_text(
                f"✅ ID Free Fire registrado: {freefire_id}\n\n"
                f"📦 Plano: {plan_info['nome']}\n"
                f"💵 Valor: {plan_info['valor']}\n\n"
                f"💳 Clique no botão abaixo para realizar o pagamento.\n"
                f"Após o pagamento, você receberá a confirmação automaticamente!\n\n"
                f"Em caso de dúvidas: @Luxzin7",
                reply_markup=InlineKeyboardMarkup(keyboard_user)
            )

        except Exception as e:
            self.logger.error(f"Erro ao processar plano pago: {e}")
            await update.message.reply_text("❌ Erro ao processar compra do plano.")

    # === HANDLERS DE ERRO ===
    async def global_error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handler global de erro"""
        error = context.error
        self.logger.error(f"Handler global de erro: {str(error)}")
        self.logger.error(traceback.format_exc())

        if isinstance(error, NetworkError):
            await self.handle_network_error(update, context, error)
        elif isinstance(error, TimedOut):
            await self.handle_timeout_error(update, context, error)
        elif isinstance(error, BadRequest):
            await self.handle_bad_request_error(update, context, error)
        else:
            await self.handle_generic_error(update, context, error)

    async def handle_network_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE, error: NetworkError):
        """Lidar com erros de rede"""
        self.logger.warning(f"Erro de rede: {str(error)}")
        if update and update.message:
            try:
                await update.message.reply_text("🌐 Problema de conectividade. Tentando reconectar...")
            except:
                pass

    async def handle_timeout_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE, error: TimedOut):
        """Lidar com erros de timeout"""
        self.logger.warning(f"Erro de timeout: {str(error)}")
        if update and update.message:
            try:
                await update.message.reply_text("⏱️ Solicitação expirou. Tente novamente.")
            except:
                pass

    async def handle_bad_request_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE, error: BadRequest):
        """Lidar com erros de solicitação inválida"""
        self.logger.warning(f"Erro de solicitação inválida: {str(error)}")
        if update and update.message:
            try:
                await update.message.reply_text(f"❌ Solicitação inválida: {str(error)[:100]}")
            except:
                pass

    async def handle_generic_error(self, update: Update, context: ContextTypes.DEFAULT_TYPE, error: Exception):
        """Lidar com erros genéricos"""
        if update and update.message:
            try:
                await update.message.reply_text("❌ Ocorreu um erro inesperado. O problema foi registrado.")
            except:
                pass

    # === UTILITÁRIOS DO SISTEMA ===
    def get_uptime(self) -> str:
        """Obter tempo de atividade"""
        if not self.start_time:
            return "Desconhecido"

        uptime_seconds = (datetime.now() - self.start_time).total_seconds()
        hours = int(uptime_seconds // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        return f"{hours}h {minutes}m"

    async def schedule_restart(self):
        """Agendar reinicialização"""
        await asyncio.sleep(2)
        await self.restart_bot()

    async def restart_bot(self):
        """Reiniciar o bot"""
        self.logger.info("Reiniciando bot...")
        self.restart_count += 1

        if self.restart_count > self.max_restarts:
            self.logger.error("Máximo de reinicializações excedido. Parando bot.")
            await self.stop_bot()
            return

        await self.stop_bot()
        await asyncio.sleep(3)
        await self.start_bot()

    async def start_bot(self):
        """Iniciar o bot"""
        try:
            if not await self.initialize_bot():
                self.logger.error("Falha ao inicializar bot")
                return False

            self.start_time = datetime.now()
            self.is_running = True

            self.logger.info("Iniciando polling...")
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()

            self.logger.info("✅ Bot iniciado com sucesso!")
            return True

        except Exception as e:
            self.logger.error(f"Falha ao iniciar bot: {str(e)}")
            return False

    async def stop_bot(self):
        """Parar o bot"""
        try:
            self.is_running = False

            if self.application and self.application.updater:
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()

            self.logger.info("Bot parado com sucesso")

        except Exception as e:
            self.logger.error(f"Erro ao parar bot: {str(e)}")

def check_firebase_credentials():
    """Verificar se as credenciais Firebase existem"""
    cred_path = "CREDENCIAIS.json"
    if not os.path.exists(cred_path):
        print(f"❌ Arquivo de credenciais Firebase não encontrado: {cred_path}")
        print("📋 Certifique-se que o arquivo CREDENCIAIS.json está no mesmo diretório")
        return False

    print(f"✅ Credenciais Firebase encontradas: {cred_path}")
    return True

async def main():
    """Função principal"""
    print("🤖 Luxzin Bot - Sistema de Vendas com Firebase")
    print("=" * 50)
    print(f"📅 Iniciado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print()

    # Verificar credenciais (opcional)
    check_firebase_credentials()

    # Criar bot manager
    bot_manager = LuxzinBotManager()

    # Configurar handlers de sinal
    def signal_handler(signum, frame):
        print(f"\n🛑 Sinal recebido {signum}, encerrando bot...")
        asyncio.create_task(bot_manager.stop_bot())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        success = await bot_manager.start_bot()
        if success:
            print("✅ Bot iniciado com sucesso!")
            print("📝 Logs disponíveis no diretório ./logs/")
            print("🔥 Firebase conectado e funcionando" if bot_manager.firebase_connected else "⚠️ Firebase não conectado - usando armazenamento temporário")
            print("🎮 Sistema de vendas integrado")
            print("💰 Planos: Grátis, 1dia, 7dias, 1mês, 90dias")
            print("👑 Sistema de admins ativo")
            print()
            print("Pressione Ctrl+C para parar o bot")
            print("=" * 50)

            # Manter o bot rodando
            while bot_manager.is_running:
                await asyncio.sleep(1)
        else:
            print("❌ Falha ao iniciar o bot")
            print("📋 Verifique:")
            print("   - Token do bot está correto")
            print("   - Credenciais Firebase estão válidas (se usando)")
            print("   - Conexão com internet está ativa")
            return 1

    except KeyboardInterrupt:
        print("\n🛑 Parando bot...")
        await bot_manager.stop_bot()
        print("✅ Bot parado com sucesso")
    except Exception as e:
        print(f"❌ Erro inesperado: {e}")
        return 1

    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
