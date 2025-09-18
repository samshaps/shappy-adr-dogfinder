# shappy-adr-dogfinder

A Python application that searches for adoptable dogs using the Petfinder API and sends daily email digests with AI-powered recommendations.

## Features

- **Petfinder Integration**: Searches for adoptable dogs within specified zip codes
- **AI-Powered Recommendations**: Uses OpenAI's API to analyze dogs and provide "Top Dogs to Consider" based on your preferences
- **Email Digest**: Sends formatted HTML emails with dog listings and recommendations
- **Customizable Preferences**: Easily modify dog preferences criteria in the code
- **Breed Filtering**: Excludes specific breeds based on your preferences

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Create a `.env` file with the following variables:
   ```
   # Petfinder API credentials
   PETFINDER_CLIENT_ID=your_petfinder_client_id_here
   PETFINDER_CLIENT_SECRET=your_petfinder_client_secret_here
   
   # Email configuration
   SMTP_HOST=smtp.gmail.com
   SMTP_PORT=587
   SMTP_USER=your_email@gmail.com
   SMTP_PASS=your_app_password_here
   SENDER_EMAIL=your_email@gmail.com
   SENDER_NAME=Dog Digest
   RECIPIENTS=email1@example.com,email2@example.com
   
   # OpenAI API key for dog analysis
   OPENAI_API_KEY=your_openai_api_key_here
   
   # Search configuration (optional)
   ZIP_CODES=08401,11211,19003
   DISTANCE_MILES=100
   ```

3. Get API keys:
   - **Petfinder**: Sign up at [Petfinder API](https://www.petfinder.com/developers/)
   - **OpenAI**: Get your API key from [OpenAI Platform](https://platform.openai.com/api-keys)

## Usage

Run the application:
```bash
python main.py
```

## Customizing Dog Preferences

Edit the `get_dog_preferences()` function in `main.py` to match your specific criteria:

```python
def get_dog_preferences() -> str:
    return """
    I'm looking for a dog with the following preferences:
    - Size: Small to medium (under 50 lbs)
    - Age: Young adult (1-4 years old) or puppy
    - Energy level: Moderate to high energy
    - Temperament: Friendly, social, good with families
    - Health: No major health issues mentioned
    - Special considerations: Good with other dogs, house-trained preferred
    - Breed preferences: Mixed breeds welcome, avoid very high-maintenance breeds
    - Personality: Playful, affectionate, trainable
    """
```

## Email Format

The application sends HTML emails with:
1. **Top Dogs to Consider**: AI-analyzed recommendations based on your preferences
2. **All Available Dogs**: Complete table of all dogs found in the search

## Requirements

- Python 3.7+
- Petfinder API access
- OpenAI API access
- SMTP email configuration
