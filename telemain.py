import asyncio
import json
import os
import threading
import base64
import logging
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from telethon import TelegramClient, events
from telethon.tl.types import User, Channel, Chat, PeerUser, PeerChat, PeerChannel
from telethon.errors import (
    SessionPasswordNeededError, 
    PhoneCodeInvalidError, 
    PasswordHashInvalidError,
    FloodWaitError,
    PhoneNumberInvalidError
)
from werkzeug.serving import make_server
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24).hex()
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading', ping_timeout=60, ping_interval=25)

# Global variables
telegram_client = None
client_status = {
    'connected': False, 
    'authenticated': False, 
    'phone': None, 
    'name': None, 
    'username': None,
    'user_id': None
}
client_loop = None
client_thread = None
pending_phone = None
phone_code_hash = None

# Configuration - REPLACE WITH YOUR CREDENTIALS
API_ID = "36608140"  # Replace with your API ID
API_HASH = "a0ef79f014d19d5f5f217afab1127330"  # Replace with your API Hash
SESSION_NAME = "web_client_session"

class TelegramWebClient:
    def __init__(self, api_id, api_hash, session_name):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.client = None
        self.loop = None
        self._message_handler_registered = False
        
    async def start_client(self):
        """Initialize and start the Telegram client"""
        try:
            self.client = TelegramClient(
                self.session_name, 
                self.api_id, 
                self.api_hash,
                connection_retries=5,
                retry_delay=1,
                auto_reconnect=True
            )
            
            await self.client.connect()
            
            if await self.client.is_user_authorized():
                me = await self.client.get_me()
                client_status.update({
                    'authenticated': True,
                    'connected': True,
                    'phone': me.phone,
                    'name': f"{me.first_name} {me.last_name or ''}".strip(),
                    'username': me.username,
                    'user_id': me.id
                })
                self.setup_message_handler()
                logger.info(f"Client authenticated as {me.phone}")
                return True
            else:
                client_status.update({
                    'connected': True,
                    'authenticated': False
                })
                logger.info("Client connected but not authenticated")
                return False
        except Exception as e:
            logger.error(f"Error starting client: {e}", exc_info=True)
            client_status.update({
                'connected': False,
                'authenticated': False
            })
            return False
        
    def setup_message_handler(self):
        """Setup handler for incoming messages"""
        if self._message_handler_registered:
            return
            
        @self.client.on(events.NewMessage)
        async def handle_new_message(event):
            try:
                sender_name = await self.get_sender_name(event.message)
                chat_name = await self.get_chat_name(event.message.peer_id)
                
                message_data = {
                    'type': 'new_message',
                    'chat_name': chat_name,
                    'sender_name': sender_name,
                    'message': event.message.message or '[Media]',
                    'timestamp': event.message.date.strftime('%H:%M:%S'),
                    'chat_id': self.get_chat_id_from_peer(event.message.peer_id)
                }
                socketio.emit('new_message', message_data)
                logger.info(f"New message in {chat_name} from {sender_name}")
            except Exception as e:
                logger.error(f"Error handling new message: {e}", exc_info=True)
        
        self._message_handler_registered = True
    
    def get_chat_id_from_peer(self, peer_id):
        """Extract chat ID from peer object"""
        if isinstance(peer_id, PeerUser):
            return peer_id.user_id
        elif isinstance(peer_id, PeerChat):
            return peer_id.chat_id
        elif isinstance(peer_id, PeerChannel):
            return peer_id.channel_id
        elif hasattr(peer_id, 'user_id'):
            return peer_id.user_id
        elif hasattr(peer_id, 'chat_id'):
            return peer_id.chat_id
        elif hasattr(peer_id, 'channel_id'):
            return peer_id.channel_id
        return None
                
    async def get_sender_name(self, message):
        """Get sender name from message"""
        try:
            sender = await message.get_sender()
            if hasattr(sender, 'first_name'):
                name = sender.first_name or "Unknown"
                if hasattr(sender, 'last_name') and sender.last_name:
                    name += f" {sender.last_name}"
                return name
            elif hasattr(sender, 'title'):
                return sender.title
            else:
                return f"User {message.sender_id}"
        except Exception as e:
            logger.error(f"Error getting sender name: {e}")
            return f"User {message.sender_id}"
            
    async def get_chat_name(self, peer_id):
        """Get chat name from peer ID"""
        try:
            entity = await self.client.get_entity(peer_id)
            if hasattr(entity, 'title'):
                return entity.title
            elif hasattr(entity, 'first_name'):
                name = entity.first_name or "Unknown"
                if hasattr(entity, 'last_name') and entity.last_name:
                    name += f" {entity.last_name}"
                return name
            else:
                return "Unknown"
        except Exception as e:
            logger.error(f"Error getting chat name: {e}")
            return "Unknown"
            
    async def send_code_request(self, phone):
        """Send verification code to phone number"""
        global pending_phone, phone_code_hash
        try:
            result = await self.client.send_code_request(phone)
            pending_phone = phone
            phone_code_hash = result.phone_code_hash
            logger.info(f"Code sent to {phone}")
            return True, None
        except PhoneNumberInvalidError:
            return False, "Invalid phone number format"
        except FloodWaitError as e:
            return False, f"Too many requests. Please wait {e.seconds} seconds"
        except Exception as e:
            logger.error(f"Error sending code: {e}", exc_info=True)
            return False, str(e)
        
    async def sign_in(self, code=None, password=None):
        """Sign in with code or password"""
        global pending_phone, phone_code_hash
        
        if not pending_phone:
            return False, "Phone number not set. Please restart login."
            
        try:
            if password:
                # 2FA password login
                await self.client.sign_in(password=password)
            elif code and phone_code_hash:
                # Code verification
                await self.client.sign_in(
                    phone=pending_phone,
                    code=code,
                    phone_code_hash=phone_code_hash
                )
            else:
                return False, "Missing code or password"
            
            me = await self.client.get_me()
            client_status.update({
                'authenticated': True,
                'connected': True,
                'phone': me.phone,
                'name': f"{me.first_name} {me.last_name or ''}".strip(),
                'username': me.username,
                'user_id': me.id
            })
            
            self.setup_message_handler()
            logger.info(f"Successfully authenticated as {me.phone}")
            return True, None
            
        except SessionPasswordNeededError:
            return False, "2fa_required"
        except PhoneCodeInvalidError:
            return False, "Invalid verification code"
        except PasswordHashInvalidError:
            return False, "Invalid password"
        except Exception as e:
            logger.error(f"Sign in error: {e}", exc_info=True)
            return False, str(e)
            
    async def get_dialogs(self, limit=100):
        """Get list of chats/dialogs"""
        try:
            dialogs = []
            async for dialog in self.client.iter_dialogs(limit=limit):
                if not dialog.entity:
                    continue
                
                # Get chat name
                if hasattr(dialog.entity, 'title'):
                    chat_name = dialog.entity.title
                elif hasattr(dialog.entity, 'first_name'):
                    chat_name = dialog.entity.first_name or "Unknown"
                    if hasattr(dialog.entity, 'last_name') and dialog.entity.last_name:
                        chat_name += f" {dialog.entity.last_name}"
                else:
                    chat_name = dialog.name or "Unknown"
                
                # Get last message
                last_message = "No messages"
                if dialog.message:
                    last_message = dialog.message.text or '[Media]'
                    if len(last_message) > 50:
                        last_message = last_message[:47] + "..."
                
                # Format date
                date_str = ""
                if dialog.date:
                    date_str = dialog.date.isoformat()
                
                dialog_info = {
                    'id': dialog.id,
                    'name': chat_name,
                    'unread_count': dialog.unread_count,
                    'last_message': last_message,
                    'date': date_str,
                    'is_user': dialog.is_user,
                    'is_group': dialog.is_group,
                    'is_channel': dialog.is_channel
                }
                dialogs.append(dialog_info)
            
            logger.info(f"Loaded {len(dialogs)} dialogs")
            return dialogs
        except Exception as e:
            logger.error(f"Error getting dialogs: {e}", exc_info=True)
            return []
        
    async def get_messages(self, chat_id, limit=50, offset_id=0):
        """Get messages from a chat"""
        try:
            messages = []
            async for message in self.client.iter_messages(
                chat_id, 
                limit=limit, 
                offset_id=offset_id
            ):
                if not (message.text or message.media):
                    continue
                    
                sender_name = await self.get_sender_name(message)
                
                # Handle media
                media_data = None
                media_type = None
                if message.media:
                    if hasattr(message.media, 'photo'):
                        try:
                            # Download photo with size limit
                            photo_bytes = await self.client.download_media(
                                message.media.photo, 
                                bytes
                            )
                            if photo_bytes:
                                # Check size and compress if needed
                                if len(photo_bytes) > 5 * 1024 * 1024:  # 5MB limit
                                    logger.warning(f"Photo too large: {len(photo_bytes)} bytes")
                                    media_data = '[Photo - too large to display]'
                                    media_type = 'photo_large'
                                else:
                                    media_data = f"data:image/jpeg;base64,{base64.b64encode(photo_bytes).decode('utf-8')}"
                                    media_type = 'photo'
                                    logger.info(f"Photo loaded: {len(photo_bytes)} bytes")
                        except Exception as e:
                            logger.error(f"Error downloading photo: {e}")
                            media_data = '[Photo - failed to load]'
                            media_type = 'photo_error'
                    elif hasattr(message.media, 'document'):
                        # Check if it's an image document
                        doc = message.media.document
                        mime_type = getattr(doc, 'mime_type', '')
                        if mime_type.startswith('image/'):
                            try:
                                # Download image document
                                img_bytes = await self.client.download_media(doc, bytes)
                                if img_bytes:
                                    if len(img_bytes) > 5 * 1024 * 1024:  # 5MB limit
                                        media_data = f'[Image - too large to display]'
                                        media_type = 'image_large'
                                    else:
                                        # Determine correct mime type for base64
                                        if 'png' in mime_type:
                                            mime = 'image/png'
                                        elif 'gif' in mime_type:
                                            mime = 'image/gif'
                                        elif 'webp' in mime_type:
                                            mime = 'image/webp'
                                        else:
                                            mime = 'image/jpeg'
                                        
                                        media_data = f"data:{mime};base64,{base64.b64encode(img_bytes).decode('utf-8')}"
                                        media_type = 'image'
                                        logger.info(f"Image document loaded: {len(img_bytes)} bytes, type: {mime_type}")
                            except Exception as e:
                                logger.error(f"Error downloading image document: {e}")
                                media_data = '[Image - failed to load]'
                                media_type = 'image_error'
                        else:
                            media_data = f'[Document: {mime_type}]'
                            media_type = 'document'
                    elif hasattr(message.media, 'webpage'):
                        # Handle web previews
                        media_data = '[Web Preview]'
                        media_type = 'webpage'
                    else:
                        media_data = '[Media]'
                        media_type = 'other'
                
                msg_info = {
                    'id': message.id,
                    'text': message.text or '[Media]',
                    'sender_name': sender_name,
                    'date': message.date.isoformat(),
                    'is_outgoing': message.out,
                    'media': media_data,
                    'media_type': media_type
                }
                messages.append(msg_info)
            
            messages.reverse()
            logger.info(f"Loaded {len(messages)} messages from chat {chat_id}")
            return messages
        except Exception as e:
            logger.error(f"Error getting messages: {e}", exc_info=True)
            return []
        
    async def send_message(self, chat_id, message_text):
        """Send a message to a chat"""
        try:
            await self.client.send_message(chat_id, message_text)
            logger.info(f"Message sent to chat {chat_id}")
            return True, "Message sent successfully"
        except Exception as e:
            logger.error(f"Error sending message: {e}", exc_info=True)
            return False, str(e)
            
    async def search_messages(self, query, limit=100):
        """Search messages across all chats"""
        try:
            results = []
            async for message in self.client.iter_messages(
                None, 
                search=query, 
                limit=limit
            ):
                if not message.text:
                    continue
                    
                chat_name = await self.get_chat_name(message.peer_id)
                sender_name = await self.get_sender_name(message)
                
                result = {
                    'text': message.text[:200],  # Limit text length
                    'chat_name': chat_name,
                    'sender_name': sender_name,
                    'date': message.date.isoformat(),
                    'chat_id': self.get_chat_id_from_peer(message.peer_id)
                }
                results.append(result)
            
            logger.info(f"Found {len(results)} results for query: {query}")
            return results
        except Exception as e:
            logger.error(f"Error searching messages: {e}", exc_info=True)
            return []
    
    async def get_profile_photo(self, chat_id):
        """Get profile photo for a chat"""
        try:
            entity = await self.client.get_entity(chat_id)
            if not entity.photo:
                return None
            
            # Download with size limit
            photo_bytes = await self.client.download_profile_photo(entity, bytes)
            if photo_bytes:
                if len(photo_bytes) > 2 * 1024 * 1024:  # 2MB limit for profile photos
                    logger.warning(f"Profile photo too large: {len(photo_bytes)} bytes")
                    return None
                return f"data:image/jpeg;base64,{base64.b64encode(photo_bytes).decode('utf-8')}"
            return None
        except Exception as e:
            logger.error(f"Error getting profile photo: {e}")
            return None
    
    async def logout(self):
        """Logout and disconnect client"""
        try:
            if self.client and self.client.is_connected():
                await self.client.log_out()
                logger.info("User logged out")
            return True
        except Exception as e:
            logger.error(f"Error during logout: {e}", exc_info=True)
            return False

def run_async_in_thread(coro, timeout=30):
    """Execute async coroutine in the client thread"""
    global client_loop
    if not client_loop or client_loop.is_closed():
        raise RuntimeError("Client loop not available")
    
    try:
        future = asyncio.run_coroutine_threadsafe(coro, client_loop)
        return future.result(timeout=timeout)
    except asyncio.TimeoutError:
        logger.error(f"Operation timed out after {timeout}s")
        raise TimeoutError(f"Operation timed out after {timeout}s")
    except Exception as e:
        logger.error(f"Error in async operation: {e}", exc_info=True)
        raise

def init_telegram_client():
    """Initialize Telegram client in a separate thread"""
    global telegram_client, client_loop, client_thread
    
    def client_thread_func():
        global client_loop, telegram_client
        client_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(client_loop)
        
        async def run_client():
            global telegram_client
            telegram_client = TelegramWebClient(API_ID, API_HASH, SESSION_NAME)
            await telegram_client.start_client()
            
            # Keep the loop running
            while True:
                await asyncio.sleep(1)
        
        try:
            client_loop.run_until_complete(run_client())
        except KeyboardInterrupt:
            logger.info("Client thread interrupted")
        except Exception as e:
            logger.error(f"Client thread error: {e}", exc_info=True)
        finally:
            if client_loop:
                client_loop.close()
    
    client_thread = threading.Thread(target=client_thread_func, daemon=True)
    client_thread.start()
    logger.info("Telegram client thread started")

# API Routes
@app.route('/')
def index():
    """Serve main page"""
    return render_template('index.html')

@app.route('/api/status')
def get_status():
    """Get current client status"""
    return jsonify(client_status)

@app.route('/api/check_login')
def check_login():
    """Check if user is logged in"""
    return jsonify({
        'logged_in': client_status.get('authenticated', False),
        'connected': client_status.get('connected', False)
    })

@app.route('/api/user_info')
def get_user_info():
    """Get current user information"""
    return jsonify({
        'success': True,
        'name': client_status.get('name', 'User'),
        'phone': client_status.get('phone'),
        'username': client_status.get('username'),
        'user_id': client_status.get('user_id')
    })

@app.route('/api/send_code', methods=['POST'])
def send_verification_code():
    """Send verification code to phone"""
    try:
        data = request.get_json()
        phone = data.get('phone', '').strip()
        
        if not phone:
            return jsonify({'success': False, 'error': 'Phone number is required'})
        
        success, error = run_async_in_thread(
            telegram_client.send_code_request(phone)
        )
        
        if success:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': error})
        
    except Exception as e:
        logger.error(f"Send code error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/verify_code', methods=['POST'])
def verify_code():
    """Verify code or password"""
    try:
        data = request.get_json()
        code = data.get('code')
        password = data.get('password')
        
        success, error = run_async_in_thread(
            telegram_client.sign_in(code=code, password=password)
        )
        
        if success:
            return jsonify({'success': True})
        return jsonify({'success': False, 'error': error})
        
    except Exception as e:
        logger.error(f"Verify code error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/dialogs')
def get_dialogs():
    """Get list of chats"""
    try:
        if not client_status.get('authenticated'):
            return jsonify({'success': False, 'error': 'Not authenticated'})
            
        dialogs = run_async_in_thread(telegram_client.get_dialogs())
        return jsonify({'success': True, 'dialogs': dialogs})
    except Exception as e:
        logger.error(f"Get dialogs error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/messages/<int:chat_id>')
def get_messages(chat_id):
    """Get messages from a chat"""
    try:
        if not client_status.get('authenticated'):
            return jsonify({'success': False, 'error': 'Not authenticated'})
            
        limit = request.args.get('limit', 50, type=int)
        offset_id = request.args.get('offset_id', 0, type=int)
        
        # Increase timeout for image loading
        messages = run_async_in_thread(
            telegram_client.get_messages(chat_id, limit, offset_id),
            timeout=120  # 2 minutes for loading images
        )
        return jsonify({'success': True, 'messages': messages})
    except TimeoutError:
        logger.error("Timeout loading messages with images")
        return jsonify({'success': False, 'error': 'Timeout loading messages. Try loading fewer messages.'})
    except Exception as e:
        logger.error(f"Get messages error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/send_message', methods=['POST'])
def send_message():
    """Send a message"""
    try:
        if not client_status.get('authenticated'):
            return jsonify({'success': False, 'error': 'Not authenticated'})
            
        data = request.get_json()
        chat_id = data.get('chat_id')
        message = data.get('message', '').strip()
        
        if not message:
            return jsonify({'success': False, 'error': 'Message is required'})
        
        success, result = run_async_in_thread(
            telegram_client.send_message(chat_id, message)
        )
        return jsonify({'success': success, 'message': result})
    except Exception as e:
        logger.error(f"Send message error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/search')
def search_messages():
    """Search messages"""
    try:
        if not client_status.get('authenticated'):
            return jsonify({'success': False, 'error': 'Not authenticated'})
            
        query = request.args.get('q', '').strip()
        limit = request.args.get('limit', 50, type=int)
        
        if not query:
            return jsonify({'success': False, 'error': 'Query is required'})
        
        results = run_async_in_thread(
            telegram_client.search_messages(query, limit),
            timeout=60
        )
        return jsonify({'success': True, 'results': results})
    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/photo/<int:chat_id>')
def get_profile_photo(chat_id):
    """Get profile photo"""
    try:
        if not client_status.get('authenticated'):
            return jsonify({'success': False, 'error': 'Not authenticated'})
            
        photo_data = run_async_in_thread(
            telegram_client.get_profile_photo(chat_id),
            timeout=30
        )
        
        if photo_data:
            return jsonify({'success': True, 'photo': photo_data})
        return jsonify({'success': False, 'error': 'No profile photo'})
    except Exception as e:
        logger.error(f"Get photo error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/logout', methods=['POST'])
def logout():
    """Logout user"""
    try:
        if telegram_client:
            run_async_in_thread(telegram_client.logout())
        
        client_status.update({
            'connected': False,
            'authenticated': False,
            'phone': None,
            'name': None,
            'username': None,
            'user_id': None
        })
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Logout error: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})

# WebSocket events
@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    emit('status', client_status)
    logger.info("Client connected via WebSocket")

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info("Client disconnected from WebSocket")

# Error handlers
@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(e):
    logger.error(f"Server error: {e}", exc_info=True)
    return jsonify({'error': 'Internal server error'}), 500

if __name__ == '__main__':
    # Validate configuration
    if API_ID == "YOUR_API_ID" or API_HASH == "YOUR_API_HASH":
        logger.error("Please configure API_ID and API_HASH")
        sys.exit(1)
    
    # Initialize client
    init_telegram_client()
    
    # Give client time to initialize
    import time
    time.sleep(2)
    
    # Start server
    logger.info("Starting server on http://0.0.0.0:5000")
    logger.info("Press Ctrl+C to stop the server")
    socketio.run(app, debug=False, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)