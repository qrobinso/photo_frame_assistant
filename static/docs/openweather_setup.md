# OpenWeather API Setup Guide

## Getting Your API Key

1. Visit [OpenWeather API](https://openweathermap.org/api)
2. Click "Sign Up" in the top right corner (or "Sign In" if you already have an account)
3. Fill out the registration form and verify your email
4. Once logged in, go to your [API keys page](https://home.openweathermap.org/api_keys)
5. You'll find your default API key listed. If not, you can generate a new one
6. Copy your API key and paste it into the "OpenWeather API Key" field in the Weather Integration settings

## Important Notes

- **API Key Activation**: New API keys may take a few hours to activate. If you get an error when testing, please wait and try again later.
- **Rate Limits**: The free tier allows up to 60 calls per minute
- **Data Updates**: Weather data is cached for the interval you specify to minimize API usage
- **Security**: Your API key is stored securely on your photo server only

## Troubleshooting

If you're having issues:

1. **Invalid API Key Error**: 
   - Verify you've copied the entire key correctly
   - Wait a few hours for new keys to activate
   - Check if your key is visible in your OpenWeather account

2. **No Weather Data**:
   - Ensure your zipcode is entered correctly
   - Verify your API key is active
   - Check your server's internet connection

3. **Wrong Temperature Units**:
   - Select your preferred units (F/C) in the Weather Integration settings
   - Changes will apply to all new weather data requests

## Support

For additional help:
- Visit [OpenWeather Support](https://openweathermap.org/api/one-call-3#start)
- Check OpenWeather's [FAQ page](https://openweathermap.org/faq)
- Review their [Terms of Service](https://openweathermap.org/terms)

## Privacy Note

The Weather Integration only sends your zipcode and API key to OpenWeather's servers to fetch local weather data. No personal information is shared or stored beyond what's necessary for the weather service to function. 