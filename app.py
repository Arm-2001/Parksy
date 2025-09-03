from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import requests
import json
import datetime
import re
import os
import random
from typing import Dict, List, Optional
import time

app = Flask(__name__)
CORS(app)

class Parksy:
    def __init__(self):
        # Get API keys from environment variables
        self.here_api_key = os.getenv('HERE_API_KEY')
        self.openrouter_api_key = os.getenv('OPENROUTER_API_KEY')
        
        # API Endpoints
        self.here_geocoding_url = "https://geocode.search.hereapi.com/v1/geocode"
        self.here_parking_url = "https://discover.search.hereapi.com/v1/discover"
        self.openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
        
        # Conversation sessions (in production, use Redis or database)
        self.conversations = {}
        
        # Enhanced system prompt for Parksy
        self.system_prompt = """You are Parksy, a friendly AI parking assistant who talks like a real person. You're knowledgeable, conversational, and genuinely want to help people with their parking struggles.

Key traits:
- You're called Parksy - embrace it! Be personable and memorable
- Respond naturally to whatever users say, never force them into specific formats
- Use casual, human language with contractions and conversational phrases
- Show empathy for parking struggles (everyone hates finding parking!)
- Adapt your response style to match the user's tone and urgency
- Remember context from your conversation with each user
- Be encouraging, positive, and sometimes a bit cheeky
- Use real parking data when available, present it clearly but don't overwhelm

Response guidelines:
- Always acknowledge what they're asking about first
- If you have parking data, present it in a helpful, scannable way
- Give practical, local advice and suggestions
- Be personal - use "you" and "I" naturally
- If they're frustrated, be understanding and supportive
- If they're in a hurry, be concise and action-oriented
- If they want to chat, be conversational and fun

Remember: You're Parksy, the parking assistant people actually want to talk to. Make finding parking a little less painful!"""

    def geocode_location(self, location: str) -> Optional[Dict]:
        """Convert location string to coordinates using HERE Geocoding API"""
        try:
            params = {
                'q': location,
                'apikey': self.here_api_key,
                'limit': 1
            }
            
            response = requests.get(self.here_geocoding_url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if data.get('items'):
                item = data['items'][0]
                return {
                    'lat': item['position']['lat'],
                    'lng': item['position']['lng'],
                    'address': item['address']['label'],
                    'city': item['address'].get('city', ''),
                    'district': item['address'].get('district', '')
                }
            return None
            
        except Exception as e:
            print(f"Geocoding error: {e}")
            return None

    def search_parking(self, lat: float, lng: float, radius: int = 1500) -> List[Dict]:
        """Enhanced parking search with multiple queries to get 10+ results"""
        all_spots = []
        
        # Multiple search queries for comprehensive results
        search_queries = [
            'parking',
            'car park', 
            'parking garage',
            'street parking',
            'parking lot'
        ]
        
        for query in search_queries:
            try:
                params = {
                    'at': f"{lat},{lng}",
                    'limit': 20,
                    'q': query,
                    'apikey': self.here_api_key
                }
                
                response = requests.get(self.here_parking_url, params=params, timeout=15)
                response.raise_for_status()
                
                data = response.json()
                
                if 'items' in data:
                    for spot in data['items']:
                        spot_data = self._process_parking_spot(spot, lat, lng)
                        if spot_data and not self._is_duplicate(spot_data, all_spots):
                            all_spots.append(spot_data)
                            
            except Exception as e:
                print(f"Search query '{query}' error: {e}")
                continue
        
        # Category-based search as backup
        if len(all_spots) < 5:
            category_ids = ['700-7600-0322', '700-7600-0323', '700-7600-0000']
            for cat_id in category_ids:
                try:
                    params = {
                        'at': f"{lat},{lng}",
                        'categories': cat_id,
                        'limit': 15,
                        'apikey': self.here_api_key
                    }
                    
                    response = requests.get(self.here_parking_url, params=params, timeout=10)
                    if response.status_code == 200:
                        data = response.json()
                        for item in data.get('items', []):
                            spot_data = self._process_parking_spot(item, lat, lng)
                            if spot_data and not self._is_duplicate(spot_data, all_spots):
                                all_spots.append(spot_data)
                except Exception:
                    continue
        
        # Sort by score and return top results
        all_spots.sort(key=lambda x: x.get('score', 0), reverse=True)
        return all_spots[:10]  # Return top 10

    def _process_parking_spot(self, spot: Dict, user_lat: float, user_lng: float) -> Optional[Dict]:
        """Process and enhance parking spot data"""
        try:
            spot_lat = spot.get('position', {}).get('lat', 0)
            spot_lng = spot.get('position', {}).get('lng', 0)
            distance = self._calculate_distance(user_lat, user_lng, spot_lat, spot_lng)
            
            # Skip if too far or invalid
            if distance > 3000 or not spot.get('title'):
                return None
                
            title = spot.get('title', 'Parking Location')
            
            # Skip non-parking related results
            title_lower = title.lower()
            if not any(term in title_lower for term in ['park', 'garage', 'car', 'space', 'charging']):
                return None
            
            # Determine parking type
            parking_type = 'parking-lot'
            if any(term in title_lower for term in ['garage', 'multi', 'story']):
                parking_type = 'parking-garage'
            elif any(term in title_lower for term in ['street', 'road', 'meter']):
                parking_type = 'on-street-parking'
            elif any(term in title_lower for term in ['electric', 'ev', 'charging']):
                parking_type = 'ev-charging'
            
            walking_time = max(1, distance // 80)  # 80m per minute
            
            spot_data = {
                'name': title,
                'address': spot.get('address', {}).get('label', 'Address not available'),
                'distance': distance,
                'walking_time': walking_time,
                'position': spot.get('position', {}),
                'parking_type': parking_type,
                'pricing': self._estimate_pricing(parking_type, distance),
                'availability': self._estimate_availability(parking_type),
                'features': self._get_features(parking_type, distance),
                'score': self._calculate_score(distance, parking_type),
                'contacts': spot.get('contacts', [])
            }
            
            return spot_data
            
        except Exception as e:
            print(f"Error processing spot: {e}")
            return None

    def _is_duplicate(self, new_spot: Dict, existing_spots: List[Dict]) -> bool:
        """Check if parking spot is duplicate based on location"""
        new_pos = new_spot.get('position', {})
        new_key = f"{new_pos.get('lat', 0):.4f},{new_pos.get('lng', 0):.4f}"
        
        for spot in existing_spots:
            pos = spot.get('position', {})
            key = f"{pos.get('lat', 0):.4f},{pos.get('lng', 0):.4f}"
            if key == new_key:
                return True
        return False

    def _estimate_pricing(self, parking_type: str, distance: int) -> Dict:
        """Estimate realistic UK parking pricing"""
        if parking_type == 'parking-garage':
            base_rate = 4.00 if distance < 500 else 3.20
        elif parking_type == 'on-street-parking':
            base_rate = 3.00 if distance < 500 else 2.40
        elif parking_type == 'ev-charging':
            base_rate = 2.80
        else:
            base_rate = 2.60 if distance < 500 else 2.00
        
        return {
            'hourly_rate': f"£{base_rate:.2f}",
            'daily_rate': f"£{base_rate * 7:.2f}",
            'estimated': True
        }

    def _estimate_availability(self, parking_type: str) -> str:
        """Estimate availability based on time and type"""
        current_hour = datetime.datetime.now().hour
        
        if 8 <= current_hour <= 18:
            if parking_type == 'on-street-parking':
                return 'Limited'
            else:
                return 'Good'
        else:
            return 'Excellent'

    def _get_features(self, parking_type: str, distance: int) -> List[str]:
        """Get parking spot features"""
        features = []
        
        if parking_type == 'parking-garage':
            features.extend(['Covered', 'Secure'])
        elif parking_type == 'ev-charging':
            features.extend(['EV Charging', 'Electric Vehicle Friendly'])
        elif parking_type == 'on-street-parking':
            features.append('Street Parking')
        
        if distance < 200:
            features.append('Very Close')
        elif distance < 500:
            features.append('Walking Distance')
            
        return features

    def _calculate_score(self, distance: int, parking_type: str) -> int:
        """Calculate recommendation score for sorting"""
        score = 50
        
        # Distance scoring
        if distance < 200:
            score += 25
        elif distance < 500:
            score += 20
        elif distance < 1000:
            score += 15
        
        # Type bonus
        if parking_type == 'parking-garage':
            score += 10
        elif parking_type == 'ev-charging':
            score += 15
        
        return max(0, min(100, score))

    def generate_mock_data(self, location_info: Dict) -> List[Dict]:
        """Generate mock data when API returns insufficient results"""
        city = location_info.get('city', 'the area')
        
        mock_spots = [
            {
                'name': f'{city} Multi-Story Car Park',
                'address': f'High Street, {city}',
                'distance': random.randint(80, 300),
                'walking_time': random.randint(2, 4),
                'parking_type': 'parking-garage',
                'pricing': {'hourly_rate': '£3.50', 'daily_rate': '£18.00'},
                'availability': 'Good',
                'score': 85,
                'features': ['Covered', 'Secure', 'CCTV']
            },
            {
                'name': f'{city} Pay & Display Zone',
                'address': f'Market Street, {city}',
                'distance': random.randint(50, 250),
                'walking_time': random.randint(1, 3),
                'parking_type': 'on-street-parking',
                'pricing': {'hourly_rate': '£2.80', 'daily_rate': 'Max 4 hours'},
                'availability': 'Limited',
                'score': 70,
                'features': ['Pay & Display', 'Time Limited']
            },
            {
                'name': f'{city} Shopping Centre Car Park',
                'address': f'Retail Park, {city}',
                'distance': random.randint(150, 400),
                'walking_time': random.randint(3, 6),
                'parking_type': 'parking-lot',
                'pricing': {'hourly_rate': '£2.20', 'daily_rate': '£12.00'},
                'availability': 'Excellent',
                'score': 75,
                'features': ['Large Capacity', 'Free Sundays']
            },
            {
                'name': f'{city} Council Car Park',
                'address': f'Town Centre, {city}',
                'distance': random.randint(200, 450),
                'walking_time': random.randint(3, 6),
                'parking_type': 'parking-lot',
                'pricing': {'hourly_rate': '£1.80', 'daily_rate': '£10.00'},
                'availability': 'Good',
                'score': 72,
                'features': ['Budget Option', 'Council Run']
            }
        ]
        
        return mock_spots

    def _calculate_distance(self, lat1: float, lng1: float, lat2: float, lng2: float) -> int:
        """Calculate distance between two points in meters"""
        from math import radians, cos, sin, asin, sqrt
        
        lat1, lng1, lat2, lng2 = map(radians, [lat1, lng1, lat2, lng2])
        
        dlat = lat2 - lat1
        dlng = lng2 - lng1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlng/2)**2
        c = 2 * asin(sqrt(a))
        r = 6371000  # Radius of earth in meters
        
        return int(c * r)

    def handle_follow_up_question(self, user_message: str, session_id: str) -> Optional[str]:
        """Handle follow-up questions about previous searches"""
        session_data = self.conversations.get(session_id, {})
        last_parking_data = session_data.get('last_parking_search')
        
        if not last_parking_data:
            return None
        
        spots = last_parking_data['spots']
        location = last_parking_data['location']
        msg_lower = user_message.lower()
        
        if any(word in msg_lower for word in ['best', 'recommend', 'suggest', 'which']):
            top_spot = spots[0]
            cheapest = min(spots, key=lambda x: float(x.get('pricing', {}).get('hourly_rate', '£99').replace('£', '')))
            closest = min(spots, key=lambda x: x.get('walking_time', 99))
            
            response = f"Based on your {location} search, here are my top picks:\n\n"
            response += f"🏆 **Overall Best:** {top_spot['name']}\n"
            response += f"   Best balance of location, price, and features\n\n"
            response += f"💰 **Cheapest:** {cheapest['name']}\n"
            response += f"   Just {cheapest['pricing']['hourly_rate']}/hour\n\n"
            response += f"🚶 **Closest:** {closest['name']}\n"
            response += f"   Only {closest['walking_time']} min walk\n\n"
            response += "What matters most to you - price, convenience, or security?"
            
            return response
        
        return None

    def generate_ai_response(self, user_input: str, parking_data: List[Dict], location_info: Dict, session_id: str) -> str:
        """Generate AI response using DeepSeek R1 via OpenRouter"""
        try:
            # Get conversation history for this session
            session_data = self.conversations.get(session_id, {})
            conversation_history = session_data.get('history', [])
            
            # Build conversation context
            conversation_context = ""
            if conversation_history:
                conversation_context = "Previous conversation:\n"
                for entry in conversation_history[-3:]:
                    conversation_context += f"User: {entry['user']}\nParksy: {entry['assistant']}\n"
                conversation_context += "\n"

            # Prepare context with parking data
            context = f"""
{conversation_context}Current query: {user_input}

Location searched: {location_info.get('address', 'Unknown location') if location_info else 'No specific location'}
Current time: {datetime.datetime.now().strftime('%A, %B %d, %Y at %I:%M %p')}

"""
            
            if parking_data:
                context += f"Found {len(parking_data)} parking options:\n\n"
                for i, spot in enumerate(parking_data, 1):
                    walking_text = f"{spot['walking_time']} min walk"
                    context += f"{i}. {spot['name']}\n"
                    context += f"   📍 {spot['address']}\n"
                    context += f"   🚶 {walking_text} • 💰 {spot['pricing']['hourly_rate']}/hour\n"
                    
                    if spot.get('features'):
                        context += f"   ✨ {', '.join(spot['features'][:3])}\n"
                    
                    context += f"   Availability: {spot['availability']}\n\n"
            else:
                context += "No parking spots found in the searched area.\n"

            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": context}
            ]
            
            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "deepseek/deepseek-r1",
                "messages": messages,
                "temperature": 0.8,
                "max_tokens": 1500,
                "top_p": 0.9
            }
            
            response = requests.post(self.openrouter_url, headers=headers, json=payload, timeout=30)
            response.raise_for_status()
            
            data = response.json()
            if 'choices' in data and data['choices']:
                return data['choices'][0]['message']['content']
            
            # Fallback response with parking data
            if parking_data:
                fallback = f"I found {len(parking_data)} great parking options for you!\n\n"
                for i, spot in enumerate(parking_data[:5], 1):
                    fallback += f"🅿️ **{i}. {spot['name']}**\n"
                    fallback += f"📍 {spot['address']}\n"
                    fallback += f"🚶 {spot['walking_time']} min walk • 💰 {spot['pricing']['hourly_rate']}/hour\n"
                    if spot.get('features'):
                        fallback += f"✨ {', '.join(spot['features'][:2])}\n"
                    fallback += "\n"
                return fallback
            
            return "I'm having trouble with my response system, but I'm here to help with parking!"
            
        except Exception as e:
            print(f"AI response error: {e}")
            if parking_data:
                return f"I found {len(parking_data)} parking options for you! The search worked but I'm having response issues. Try asking 'which is best?' for recommendations."
            return "I'm having some technical difficulties right now. Could you try asking again?"

    def extract_location_from_query(self, user_input: str) -> Optional[str]:
        """Extract location from user query using improved patterns"""
        patterns = [
            r"(?:at|near|in|around|by|close to|next to)\s+([^?.,!]+?)(?:\s+(?:at|for|during)|\s*[?.,!]|$)",
            r"park\s+(?:at|near|in|around|by|close to|next to)\s+([^?.,!]+?)(?:\s+(?:at|for|during)|\s*[?.,!]|$)",
            r"parking\s+(?:at|near|in|around|by|close to|next to)\s+([^?.,!]+?)(?:\s+(?:at|for|during)|\s*[?.,!]|$)",
            r"(?:where|how|can)\s+.*?(?:at|near|in|around|by)\s+([^?.,!]+?)(?:\s*[?.,!]|$)",
            r"going\s+to\s+([^?.,!]+?)(?:\s+(?:at|for|during)|\s*[?.,!]|$)",
            r"visiting\s+([^?.,!]+?)(?:\s+(?:at|for|during)|\s*[?.,!]|$)",
            r"spots?\s+(?:in|at|near)\s+([^?.,!]+?)(?:\s*[?.,!]|$)",
            r"spaces?\s+(?:in|at|near)\s+([^?.,!]+?)(?:\s*[?.,!]|$)"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, user_input, re.IGNORECASE)
            if match:
                location = match.group(1).strip()
                if location.lower() not in ['there', 'here', 'it', 'this', 'that', 'a', 'the']:
                    return location
        
        return None

    def process_query(self, user_input: str, session_id: str = "default") -> str:
        """Process user query and return response"""
        # Initialize session if needed
        if session_id not in self.conversations:
            self.conversations[session_id] = {'history': [], 'last_parking_search': None}
        
        # Check for follow-up questions first
        follow_up = self.handle_follow_up_question(user_input, session_id)
        if follow_up:
            self.conversations[session_id]['history'].append({'user': user_input, 'assistant': follow_up})
            return follow_up
        
        # Extract location for specific searches
        location = self.extract_location_from_query(user_input)
        
        if location:
            # Geocode the location
            location_info = self.geocode_location(location)
            if not location_info:
                response = f"Hmm, I'm having trouble finding '{location}'. Could you be a bit more specific? Maybe include a street address or a well-known landmark?"
                self.conversations[session_id]['history'].append({'user': user_input, 'assistant': response})
                return response
            
            # Search for parking
            parking_data = self.search_parking(location_info['lat'], location_info['lng'])
            
            # Add mock data if insufficient results
            if len(parking_data) < 5:
                mock_data = self.generate_mock_data(location_info)
                parking_data.extend(mock_data)
                parking_data = parking_data[:10]  # Limit to 10
            
            # Store parking data for follow-up questions
            self.conversations[session_id]['last_parking_search'] = {
                'spots': parking_data,
                'location': location_info.get('city', location)
            }
            
            # Generate AI response
            ai_response = self.generate_ai_response(user_input, parking_data, location_info, session_id)
            self.conversations[session_id]['history'].append({'user': user_input, 'assistant': ai_response})
            return ai_response
        
        else:
            # Handle general conversation
            try:
                session_data = self.conversations[session_id]
                conversation_history = session_data.get('history', [])
                
                conversation_context = ""
                if conversation_history:
                    conversation_context = "Previous conversation:\n"
                    for entry in conversation_history[-2:]:
                        conversation_context += f"User: {entry['user']}\nParksy: {entry['assistant']}\n"

                messages = [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"{conversation_context}\nUser just said: {user_input}\n\nRespond naturally as Parksy. If it's not parking-related, gently guide toward how you can help with parking."}
                ]
                
                headers = {
                    "Authorization": f"Bearer {self.openrouter_api_key}",
                    "Content-Type": "application/json"
                }
                
                payload = {
                    "model": "deepseek/deepseek-r1:free",
                    "messages": messages,
                    "temperature": 0.8,
                    "max_tokens": 600
                }
                
                response = requests.post(self.openrouter_url, headers=headers, json=payload, timeout=30)
                response.raise_for_status()
                
                data = response.json()
                if 'choices' in data and data['choices']:
                    ai_response = data['choices'][0]['message']['content']
                    self.conversations[session_id]['history'].append({'user': user_input, 'assistant': ai_response})
                    return ai_response
                    
            except Exception as e:
                print(f"Chat error: {e}")
                
            return "Hey! I'm Parksy, your parking assistant. What can I help you find today?"

# Initialize Parksy
parksy = Parksy()

# Simple HTML template for testing
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Parksy - Your Parking Assistant</title>
    <style>
        body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
        .chat-container { border: 1px solid #ccc; height: 400px; overflow-y: auto; padding: 10px; margin-bottom: 10px; }
        .message { margin-bottom: 10px; }
        .user { color: blue; }
        .parksy { color: green; }
        #messageInput { width: 70%; padding: 10px; }
        #sendButton { width: 25%; padding: 10px; }
    </style>
</head>
<body>
    <h1>🅿️ Parksy - Your Parking Assistant</h1>
    <div class="chat-container" id="chatContainer">
        <div class="message parksy">Hey! I'm Parksy, your parking assistant. Where are you looking to park?</div>
    </div>
    <div>
        <input type="text" id="messageInput" placeholder="Ask me about parking anywhere..." onkeypress="if(event.key==='Enter') sendMessage()">
        <button id="sendButton" onclick="sendMessage()">Send</button>
    </div>

    <script>
        function sendMessage() {
            const input = document.getElementById('messageInput');
            const message = input.value.trim();
            if (!message) return;

            addMessage('user', message);
            input.value = '';

            fetch('/api/chat', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({message: message})
            })
            .then(response => response.json())
            .then(data => {
                addMessage('parksy', data.response);
            })
            .catch(error => {
                addMessage('parksy', 'Sorry, I had trouble processing that. Please try again!');
            });
        }

        function addMessage(sender, message) {
            const container = document.getElementById('chatContainer');
            const div = document.createElement('div');
            div.className = `message ${sender}`;
            div.innerHTML = `<strong>${sender === 'user' ? 'You' : 'Parksy'}:</strong> ${message.replace(/\\n/g, '<br>')}`;
            container.appendChild(div);
            container.scrollTop = container.scrollHeight;
        }
    </script>
</body>
</html>
"""

# Flask Routes
@app.route('/api/chat', methods=['POST'])
def chat():
    """Handle chat messages via API"""
    try:
        data = request.get_json()
        user_message = data.get('message', '').strip()
        session_id = data.get('session_id', 'web_session')
        
        if not user_message:
            return jsonify({'error': 'No message provided'}), 400
        
        response = parksy.process_query(user_message, session_id)
        
        return jsonify({
            'response': response,
            'session_id': session_id,
            'timestamp': datetime.datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({'error': f'An error occurred: {str(e)}'}), 500

@app.route('/health')
def health():
    """Health check endpoint for Render"""
    return jsonify({'status': 'healthy', 'service': 'Parksy AI'})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
