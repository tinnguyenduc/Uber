from flask import render_template, request, flash, redirect, url_for, jsonify, session
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email
import requests
from pymongo import MongoClient
from bson import ObjectId
import sqlite3
from app import app
from app.models import User  
from app.database import setup_sqlite
from flask_socketio import SocketIO, emit
from flask_mail import Mail, Message
from app.forms import RegistrationForm, LoginForm
from app import Config 
from app.database import setup_sqlite, setup_mongo
from app.auth import login_manager
import os
from flask_debugtoolbar import DebugToolbarExtension
import time

toolbar = DebugToolbarExtension(app)

app.config.from_object(Config)

mail = Mail(app)

socketio = SocketIO(app)

db, chat_collection = setup_mongo()

login_manager.init_app(app)

setup_sqlite()

GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

@app.route('/register', methods=['GET', 'POST'])
def register():
    form = RegistrationForm()

    if form.validate_on_submit():
        start_time = time.time()

        username = form.username.data
        password = form.password.data
        email = form.email.data
        role = request.args.get('role', 'user')  

        with sqlite3.connect("database.db") as con:
            cur = con.cursor()
            try:
                cur.execute("INSERT INTO users (username, password, email, role) VALUES (?, ?, ?, ?)", (username, password, email, role))
                con.commit()
                flash("User created successfully. Please login.", "success")
                end_time = time.time()
                response_time = end_time - start_time
                return render_template('register.html', form=form, response_time=response_time)
            except sqlite3.Error as e:
                flash(f"Error in user creation: {str(e)}", "danger")

    return render_template('register.html', form=form)


@app.route('/login', methods=["GET", "POST"])
def login():
    form = LoginForm()

    setup_sqlite()

    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        role = request.args.get('role', 'user')  

        with sqlite3.connect("database.db") as con:
            con.row_factory = sqlite3.Row
            cur = con.cursor()
            cur.execute("SELECT * FROM users WHERE username=? AND role=?", (username, role))
            user_row = cur.fetchone()

        if user_row:
            print("User found:", user_row)
            if user_row["password"] == password:
                print("Password matches")

                user_email = user_row['email']

                user_obj = User(user_row["id"], user_row["username"], user_row["password"], user_row["role"], user_email)
                login_user(user_obj)

                session["username"] = username
                session["role"] = role
                session["email"] = user_email

                flash("Login successful!", "success")
                return redirect(url_for('home', role=role))  
            else:
                print("Incorrect password")
        else:
            print("User not found")
        flash("Incorrect username or password. Please try again.", "danger")

    return render_template('login.html', form=form)


@app.route('/home')
@login_required
def home():
    start_time = time.time()

    role = session.get('role', 'user')

    if role == 'user':
        end_time = time.time()
        response_time = end_time - start_time

        return render_template('user_home.html', response_time=response_time)
    elif role == 'driver':
        ride_requests = db['rides'].find({"status": "requested"})
        end_time = time.time()
        response_time = end_time - start_time

        return render_template('driver_home.html', user=current_user, ride_requests=ride_requests, response_time=response_time)
    else:
        end_time = time.time()
        response_time = end_time - start_time

        return redirect(url_for('welcome', response_time=response_time))


@app.route('/')
def welcome():
    return render_template('welcome.html')

from flask import request, jsonify

from flask import session

@app.route('/save_ride', methods=['POST'])
def save_ride():
    ride_data = request.get_json()

    # Extract other ride information
    username = ride_data.get('username')
    selected_car_html = ride_data.get('selectedCarHtml')
    status = ride_data.get('status')
    origin = ride_data.get('origin')
    destination = ride_data.get('destination')
    origin_lat = ride_data.get('origin_lat')
    origin_lng = ride_data.get('origin_lng')
    trip_cost = ride_data.get('tripCost') 

    rides_collection = db["rides"]

    ride_entry = {
        "username": username,
        "selected_car_html": selected_car_html,
        "status": status,
        "origin": origin,
        "destination": destination,
        "origin_lat": origin_lat,
        "origin_lng": origin_lng,
        "trip_cost": trip_cost  
    }

    result = rides_collection.insert_one(ride_entry)

    session['trip_cost'] = trip_cost

    if result.inserted_id:
        return jsonify({"status": "success", "message": "Ride information saved successfully"})
    else:
        return jsonify({"status": "error", "message": "Failed to save ride information"})

@app.route('/confirm_ride/<ride_id>', methods=['POST'])
@login_required
def confirm_ride(ride_id):
    try:
        result = db['rides'].update_one({'_id': ObjectId(ride_id), 'status': 'requested'}, {'$set': {'status': 'confirmed'}})

        if result.modified_count > 0:
            session['confirmed_ride_id'] = ride_id
            return jsonify({"status": "success", "message": "Ride confirmed"})
        else:
            return jsonify({"status": "error", "message": "Ride is already confirmed or does not exist"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"Error confirming ride: {str(e)}"})

@app.route('/chat')
@login_required
def chat():
    role = session.get('role', 'user')
    current_username = current_user.username

    chat_history = db['chat_history'].find(
        {"$or": [{"sender": current_username}, {"receiver": current_username}]},
        {"_id": 0}
    ).sort("timestamp", 1)  

    confirmed_ride_id = session.get('confirmed_ride_id')

    requested_ride_id = session.get('requested_ride_id')

    return render_template('chat.html', user=current_user, role=role, chat_history=chat_history, confirmed_ride_id=confirmed_ride_id, requested_ride_id=requested_ride_id)

@socketio.on('send_message')
def handle_message(messageData):
    sender = messageData['sender']
    receiver = messageData['receiver']
    message = messageData['message']
    timestamp = messageData['timestamp']
    ride_id = messageData.get('ride_id')
    trip_cost = messageData.get('trip_cost') 

    # Save the chat message in MongoDB
    chat_history_collection = db['chat_history']
    chat_history_collection.insert_one({
        "sender": sender,
        "receiver": receiver,
        "message": message,
        "timestamp": timestamp,
        "ride_id": ride_id,
        "trip_cost": trip_cost  
    })

    emit('receive_message', messageData, broadcast=True)

@app.route('/user_home', methods=['POST'])
@login_required
def user_home():
    username = current_user.username

    origin = request.form.get('origin')
    destination = request.form.get('destination')
    travel_mode = request.form.get('travel_mode')

    if not origin or not destination or not travel_mode:
        return jsonify({"status": "error", "message": "Missing input parameters"})

    api_key = os.getenv('GOOGLE_MAPS_API_KEY')
    url = f'https://maps.googleapis.com/maps/api/distancematrix/json?units=metric&origins={origin}&destinations={destination}&mode={travel_mode}&key={api_key}'

    try:
        response = requests.get(url)
        data = response.json()
        # Extract relevant information from the API response
        distance = data['rows'][0]['elements'][0]['distance']['text']
        duration = data['rows'][0]['elements'][0]['duration']['text']
    except requests.exceptions.RequestException as e:
        return jsonify({"status": "error", "message": f"Error: {str(e)}"})

    ride_request_data = {
        "username": username,
        "origin": origin,
        "destination": destination,
        "travel_mode": travel_mode,
        "status": "requested",
        "distance": distance,
        "duration": duration
    }

    session['requested_ride_id'] = str(ride_request_data)

    return redirect(url_for('chat'))

@app.route('/payment')
@login_required
def payment():
    trip_cost = request.args.get('trip_cost')

    if trip_cost is not None:
        user_email = current_user.email

        return render_template('payment.html', trip_cost=trip_cost, user_email=user_email)
    else:
        flash("No trip cost available.", "error")
        return render_template('payment.html', trip_cost=None, user_email=None)

def get_user_email(user_id):
    with sqlite3.connect("database.db") as con:
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("SELECT email FROM users WHERE id=?", (user_id,))
        user_data = cur.fetchone()

    return user_data['email'] if user_data else None

def send_payment_confirmation_email(to_email, payment_info):
    subject = 'Invoice'
    body = f'Thank you for your payment. Ride Cost:\n\n{payment_info}'

    msg = Message(subject, sender='your-email@gmail.com', recipients=[to_email])
    msg.body = body

    mail.send(msg)

@app.route('/confirm_payment', methods=['POST'])
@login_required
def confirm_payment():
    try:
        user_email = get_user_email(current_user.id)

        if user_email:
            trip_cost = session.get('trip_cost', 'N/A')

            if trip_cost != 'N/A':
                send_payment_confirmation_email(user_email, trip_cost)

                flash("Payment confirmed. An email has been sent to your email address.", "success")
                return redirect(url_for('home'))  
            else:
                flash("Failed to retrieve trip cost. Please contact support.", "danger")
        else:
            flash("Failed to send email. Please contact support.", "danger")
    except Exception as e:
        flash(f"Error confirming payment: {str(e)}", "danger")

    return redirect(url_for('payment')) 

if __name__ == '__main__':
    socketio.run(app, debug=True)

@app.route('/earnings')
@login_required
def earnings():
    ride_id = session.get('confirmed_ride_id')

    if ride_id:
        ride_data = db['rides'].find_one({'_id': ObjectId(ride_id)})

        if ride_data:
            trip_cost = ride_data.get('trip_cost')

            if trip_cost is not None:
                return render_template('earnings.html', trip_cost=trip_cost, ride_id=ride_id)
    
    return render_template('earnings.html', trip_cost=None, ride_id=None)

def send_payment_confirmation_email(to_email, payment_info):
    subject = 'Payment Confirmation'
    body = f'Thank you for your payment. Here is your payment information:\n\n{payment_info}'

    msg = Message(subject, sender='your-email@example.com', recipients=[to_email])
    msg.body = body

    mail.send(msg)

if __name__ == '__main__':
    start_time = time.time()
    socketio.run(app, debug=True)
    end_time = time.time()

    total_time = end_time - start_time
    print(f"Total time taken: {total_time} seconds")
