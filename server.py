import os
from flask import Flask, render_template, request, session
from flask_socketio import SocketIO, join_room, leave_room, emit
from dotenv import load_dotenv

# Load environment variables for secure key management
load_dotenv()

app = Flask(__name__, template_folder='public')
# Use the SECRET_KEY from environment variables for session security
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'a_default_secret_key_for_development')
# Initialize Flask-SocketIO with eventlet for asynchronous operations
socketio = SocketIO(app, async_mode='eventlet')

# In-memory storage for rooms, messages, and users
# This simple structure is not persistent and will be cleared on server restart.
rooms = {}
MAX_MESSAGES = 200
OWNER_IP = os.getenv('OWNER_IP')

@app.route('/')
def index():
    """Serves the main HTML file for the chat application."""
    return render_template('index.html')

@socketio.on('join')
def on_join(data):
    """
    Handles a user joining a chat room.
    """
    room_code = data.get('room')
    nickname = data.get('nickname')
    max_strength = data.get('max_strength', 2)
    sid = request.sid
    ip = request.remote_addr

    if not room_code or not nickname:
        emit('join_error', {'error': 'Room code and nickname are required.'})
        return

    if room_code not in rooms:
        rooms[room_code] = {
            'users': {},
            'messages': [],
            'theme': 'dark-neon',
            'max_strength': int(max_strength)
        }
        role = 'owner'
    else:
        role = 'member'
    
    room = rooms[room_code]

    if len(room['users']) >= room['max_strength'] and sid not in room['users']:
        emit('join_error', {'error': 'This room is full.'})
        return

    if ip == OWNER_IP:
        role = 'owner'

    join_room(room_code)
    room['users'][sid] = {'nickname': nickname, 'role': role, 'ip': ip}
    session['room'] = room_code
    session['nickname'] = nickname
    session['sid'] = sid

    user_list = {s: u for s, u in room['users'].items()}

    emit('join_success', {
        'messages': room['messages'],
        'users': user_list,
        'sid': sid,
        'theme': room['theme'],
        'max_strength': room['max_strength']
    })

    emit('update_user_list', {'users': user_list}, room=room_code)

@socketio.on('send_message')
def on_send_message(data):
    """
    Handles receiving a message from a user.
    """
    room_code = session.get('room')
    nickname = session.get('nickname')
    sid = session.get('sid')

    if not room_code or not sid or room_code not in rooms:
        return

    message_data = {
        'id': data['id'],
        'sid': sid,
        'nickname': nickname,
        'message': data['message'],
        'timestamp': data['timestamp'],
        'reply_to': data.get('reply_to')
    }
    room = rooms[room_code]
    room['messages'].append(message_data)
    room['messages'] = room['messages'][-MAX_MESSAGES:]

    emit('receive_message', message_data, room=room_code)

@socketio.on('delete_message')
def on_delete_message(data):
    """
    Handles a request to delete a message.
    """
    room_code = session.get('room')
    sid = session.get('sid')
    message_id = data.get('id')

    if room_code in rooms and message_id and sid:
        room = rooms[room_code]
        user = room['users'].get(sid)
        message_to_delete = next((msg for msg in room['messages'] if msg.get('id') == message_id), None)

        if user and message_to_delete and (user['role'] in ['owner', 'admin'] or message_to_delete.get('sid') == sid):
            if not (user['role'] == 'admin' and room['users'].get(message_to_delete.get('sid'), {}).get('role') == 'owner'):
                room['messages'].remove(message_to_delete)
                emit('message_deleted', {'id': message_id}, room=room_code)

@socketio.on('edit_message')
def on_edit_message(data):
    """
    Handles a request to edit a message.
    """
    room_code = session.get('room')
    sid = session.get('sid')
    message_id = data.get('id')
    new_content = data.get('message')

    if room_code in rooms and message_id and new_content and sid:
        room = rooms[room_code]
        message_to_edit = next((msg for msg in room['messages'] if msg.get('id') == message_id and msg.get('sid') == sid), None)
        
        if message_to_edit:
            message_to_edit['message'] = new_content
            message_to_edit['edited'] = True
            emit('message_edited', message_to_edit, room=room_code)

@socketio.on('change_theme')
def on_change_theme(data):
    """
    Handles theme change requests.
    """
    room_code = session.get('room')
    theme = data.get('theme')

    if room_code in rooms and theme:
        rooms[room_code]['theme'] = theme
        emit('theme_changed', {'theme': theme}, room=room_code)

@socketio.on('clear_chat')
def on_clear_chat():
    """
    Handles a request to clear the chat history for a room.
    """
    room_code = session.get('room')
    sid = request.sid
    if room_code in rooms and rooms[room_code]['users'].get(sid, {}).get('role') == 'owner':
        rooms[room_code]['messages'] = []
        emit('chat_cleared', room=room_code)

@socketio.on('typing')
def on_typing(data):
    """Broadcasts a user's typing status to others in the room."""
    room_code = session.get('room')
    if room_code in rooms:
        emit('typing_status', {
            'sid': session.get('sid'),
            'nickname': session.get('nickname'),
            'is_typing': data['is_typing']
        }, room=room_code, include_self=False)

@socketio.on('disconnect')
def on_disconnect():
    """
    Handles a user disconnecting.
    """
    room_code = session.get('room')
    sid = request.sid

    if room_code in rooms and sid in rooms[room_code]['users']:
        was_owner = rooms[room_code]['users'][sid]['role'] == 'owner'
        del rooms[room_code]['users'][sid]
        leave_room(room_code)

        if not rooms[room_code]['users']:
            del rooms[room_code]
        else:
            if was_owner and not any(u['role'] == 'owner' for u in rooms[room_code]['users'].values()):
                new_owner_sid = next(iter(rooms[room_code]['users']))
                rooms[room_code]['users'][new_owner_sid]['role'] = 'owner'
                emit('role_changed', {'sid': new_owner_sid, 'role': 'owner'}, room=room_code)
            
            emit('update_user_list', {'users': {s: u for s, u in rooms[room_code]['users'].items()}}, room=room_code)

@socketio.on('kick_user')
def on_kick_user(data):
    """Handles a request to kick a user from the room."""
    room_code = session.get('room')
    kicker_sid = session.get('sid')
    kicked_sid = data.get('sid')

    if room_code in rooms and kicker_sid in rooms[room_code]['users'] and kicked_sid in rooms[room_code]['users']:
        kicker = rooms[room_code]['users'][kicker_sid]
        kicked = rooms[room_code]['users'][kicked_sid]

        if (kicker['role'] == 'owner' and kicked['role'] != 'owner') or \
           (kicker['role'] == 'admin' and kicked['role'] == 'member'):
            emit('kicked', {'reason': f'You have been kicked by {kicker["nickname"]}.'}, room=kicked_sid)
            socketio.close_room(kicked_sid)

@socketio.on('change_role')
def on_change_role(data):
    """Handles a request to change a user's role."""
    room_code = session.get('room')
    changer_sid = session.get('sid')
    target_sid = data.get('sid')
    new_role = data.get('role')

    if room_code in rooms and changer_sid in rooms[room_code]['users'] and target_sid in rooms[room_code]['users']:
        changer = rooms[room_code]['users'][changer_sid]
        target = rooms[room_code]['users'][target_sid]

        if changer['role'] == 'owner' and target['role'] != 'owner':
            target['role'] = new_role
            emit('role_changed', {'sid': target_sid, 'role': new_role}, room=room_code)
            emit('update_user_list', {'users': {s: u for s, u in rooms[room_code]['users'].items()}}, room=room_code)

@socketio.on('change_max_strength')
def on_change_max_strength(data):
    """Handles a request to change the max strength of a room."""
    room_code = session.get('room')
    sid = session.get('sid')
    new_strength = data.get('strength')

    if room_code in rooms and sid in rooms[room_code]['users'] and rooms[room_code]['users'][sid]['role'] == 'owner':
        try:
            strength = int(new_strength)
            if strength > 0:
                rooms[room_code]['max_strength'] = strength
                emit('max_strength_changed', {'strength': strength}, room=room_code)
        except (ValueError, TypeError):
            pass

if __name__ == '__main__':
    import eventlet
    import eventlet.wsgi
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting server on http://localhost:{port}")
    eventlet.wsgi.server(eventlet.listen(('', port)), app)
