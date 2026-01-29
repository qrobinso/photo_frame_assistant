# Local AI Integration

This document explains how the Photo Frame Assistant integrates with local AI servers for photo analysis and generation.

## Overview

The Photo Frame Assistant supports integration with local AI servers, allowing users to perform AI-powered operations without relying on external cloud services. This provides several benefits:

- **Privacy**: Photos and data remain on your local network
- **Cost Savings**: No usage fees for external AI APIs
- **Offline Operation**: Works without internet connectivity
- **Customization**: Use specialized models for specific needs

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│                 │     │                 │     │                 │
│  Photo Frame    │     │  Photo Frame    │     │  Local AI       │
│  Assistant      │◄───►│  AI Integration │◄───►│  Server         │
│  Server         │     │                 │     │                 │
│                 │     │                 │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

## Supported AI Operations

The local AI integration supports the following operations:

1. **Photo Analysis**
   - Object detection and identification
   - Scene classification
   - Descriptive captioning
   - Aesthetic quality assessment
   - Face detection and recognition

2. **Image Generation**
   - Text-to-image generation
   - Image-to-image transformation
   - Style transfer
   - Image inpainting and outpainting

## Local AI Server Options

The Photo Frame Assistant can integrate with several local AI server options:

### 1. LocalAI

[LocalAI](https://github.com/go-skynet/LocalAI) is an open-source project that provides a drop-in replacement REST API for various AI models, compatible with OpenAI API.

**Features**:
- OpenAI API compatibility
- Support for multiple model types
- Low resource requirements
- Docker deployment

**Supported Models**:
- Stable Diffusion (image generation)
- CLIP (image understanding)
- LLaMA (text generation)
- Whisper (speech recognition)

### 2. ComfyUI

[ComfyUI](https://github.com/comfyanonymous/ComfyUI) is a powerful and modular stable diffusion GUI and backend.

**Features**:
- Node-based workflow
- Advanced image generation capabilities
- API for integration
- Extensive customization

**Supported Models**:
- Stable Diffusion models
- ControlNet
- Custom models

### 3. Ollama

[Ollama](https://github.com/ollama/ollama) allows you to run open-source large language models locally.

**Features**:
- Simple setup and usage
- API for integration
- Model library
- Low resource versions available

**Supported Models**:
- Llama 2
- Mistral
- Vicuna
- And others

## Integration Architecture

The local AI integration uses a modular architecture:

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│                  AI Integration Manager                 │
│                                                         │
└───────────────────────────┬─────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
┌───────────▼───────┐ ┌─────▼─────────┐ ┌───▼─────────────┐
│                   │ │               │ │                 │
│  OpenAI API       │ │  LocalAI      │ │  Custom API     │
│  Adapter          │ │  Adapter      │ │  Adapter        │
│                   │ │               │ │                 │
└───────────────────┘ └───────────────┘ └─────────────────┘
```

This architecture allows the Photo Frame Assistant to seamlessly switch between different AI backends based on availability and user preferences.

## Setup and Configuration

### Local AI Server Setup

#### Option 1: LocalAI

1. Install LocalAI:
   ```bash
   docker run -p 8080:8080 localai/localai:latest
   ```

2. Download models:
   ```bash
   curl -X POST http://localhost:8080/models/apply -d '{
     "url": "github:go-skynet/model-gallery/stable-diffusion-v1.5.yaml"
   }'
   ```

#### Option 2: ComfyUI

1. Install ComfyUI:
   ```bash
   git clone https://github.com/comfyanonymous/ComfyUI
   cd ComfyUI
   pip install -r requirements.txt
   ```

2. Start ComfyUI with API:
   ```bash
   python main.py --listen 0.0.0.0 --port 8188
   ```

### Photo Frame Assistant Configuration

Configure the local AI integration in the Photo Frame Assistant:

1. Navigate to "Settings" > "AI Integration"
2. Select "Local AI Server" as the AI provider
3. Enter the server URL (e.g., `http://localhost:8080`)
4. Select the models to use for different operations
5. Test the connection
6. Save the configuration

### Configuration File

The local AI integration settings are stored in `ai_settings.json`:

```json
{
  "provider": "local",
  "local_server": {
    "url": "http://localhost:8080",
    "api_type": "openai_compatible",
    "timeout": 60
  },
  "models": {
    "image_generation": "stable-diffusion-v1.5",
    "image_analysis": "clip-vit-base-patch32",
    "text_generation": "llama2"
  },
  "parameters": {
    "image_generation": {
      "width": 512,
      "height": 512,
      "num_inference_steps": 30,
      "guidance_scale": 7.5
    }
  }
}
```

## API Integration

### Image Generation

The Photo Frame Assistant communicates with the local AI server for image generation:

```python
def generate_image(prompt, model=None, size=None, parameters=None):
    """Generate an image using the local AI server."""
    # Load AI settings
    settings = load_ai_settings()
    
    # Use default model if not specified
    if not model:
        model = settings['models']['image_generation']
    
    # Use default size if not specified
    if not size:
        size = (
            settings['parameters']['image_generation']['width'],
            settings['parameters']['image_generation']['height']
        )
    
    # Prepare request parameters
    api_type = settings['local_server']['api_type']
    url = settings['local_server']['url']
    
    if api_type == 'openai_compatible':
        # OpenAI-compatible API
        endpoint = f"{url}/v1/images/generations"
        payload = {
            "model": model,
            "prompt": prompt,
            "n": 1,
            "size": f"{size[0]}x{size[1]}",
            "response_format": "b64_json"
        }
        
        # Add additional parameters if provided
        if parameters:
            payload.update(parameters)
        
        # Send request
        response = requests.post(
            endpoint,
            json=payload,
            timeout=settings['local_server']['timeout']
        )
        
        # Process response
        if response.status_code == 200:
            result = response.json()
            image_data = result['data'][0]['b64_json']
            
            # Decode base64 image
            image_bytes = base64.b64decode(image_data)
            
            # Create PIL Image
            image = Image.open(io.BytesIO(image_bytes))
            
            return image
        else:
            raise Exception(f"Error generating image: {response.text}")
    
    elif api_type == 'comfyui':
        # ComfyUI API
        # Implementation depends on ComfyUI workflow
        pass
    
    else:
        raise Exception(f"Unsupported API type: {api_type}")
```

### Image Analysis

The Photo Frame Assistant uses the local AI server for image analysis:

```python
def analyze_image(image_path, model=None):
    """Analyze an image using the local AI server."""
    # Load AI settings
    settings = load_ai_settings()
    
    # Use default model if not specified
    if not model:
        model = settings['models']['image_analysis']
    
    # Prepare request parameters
    api_type = settings['local_server']['api_type']
    url = settings['local_server']['url']
    
    # Read image file
    with open(image_path, 'rb') as f:
        image_data = f.read()
    
    # Encode image as base64
    image_b64 = base64.b64encode(image_data).decode('utf-8')
    
    if api_type == 'openai_compatible':
        # OpenAI-compatible API
        endpoint = f"{url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image in detail."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                    ]
                }
            ],
            "max_tokens": 300
        }
        
        # Send request
        response = requests.post(
            endpoint,
            json=payload,
            timeout=settings['local_server']['timeout']
        )
        
        # Process response
        if response.status_code == 200:
            result = response.json()
            description = result['choices'][0]['message']['content']
            
            # Process description to extract structured information
            analysis_result = {
                "description": description,
                "tags": extract_tags_from_description(description),
                "categories": extract_categories_from_description(description)
            }
            
            return analysis_result
        else:
            raise Exception(f"Error analyzing image: {response.text}")
    
    elif api_type == 'clip':
        # Direct CLIP API
        endpoint = f"{url}/clip/analyze"
        payload = {
            "image": image_b64,
            "model": model
        }
        
        # Send request
        response = requests.post(
            endpoint,
            json=payload,
            timeout=settings['local_server']['timeout']
        )
        
        # Process response
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Error analyzing image: {response.text}")
    
    else:
        raise Exception(f"Unsupported API type: {api_type}")
```

## Performance Considerations

### Hardware Requirements

Local AI operations can be resource-intensive. Recommended hardware:

- **CPU**: 4+ cores for basic operations
- **RAM**: 8GB minimum, 16GB+ recommended
- **GPU**: Recommended for image generation (NVIDIA with CUDA support)
- **Storage**: 10GB+ for models and generated content

### Optimization Techniques

The integration includes several optimization techniques:

1. **Model Quantization**: Using 4-bit or 8-bit quantized models to reduce memory usage
2. **Batch Processing**: Processing multiple images in batches when possible
3. **Caching**: Caching results to avoid redundant processing
4. **Scheduled Operations**: Running intensive operations during low-usage periods

## Troubleshooting

### Common Issues

1. **Connection Errors**
   - Verify the local AI server is running
   - Check network connectivity
   - Ensure correct URL in configuration

2. **Timeout Errors**
   - Increase timeout setting in configuration
   - Check server resource usage
   - Consider using lighter models

3. **Model Loading Errors**
   - Verify model files exist and are not corrupted
   - Check model compatibility with server
   - Ensure sufficient disk space and memory

### Logging and Debugging

The integration includes comprehensive logging:

```python
def log_ai_operation(operation_type, parameters, success, error=None):
    """Log AI operation for debugging."""
    log_entry = {
        'timestamp': datetime.utcnow().isoformat(),
        'operation': operation_type,
        'parameters': parameters,
        'success': success
    }
    
    if error:
        log_entry['error'] = str(error)
    
    # Write to log file
    with open('logs/ai_operations.log', 'a') as f:
        f.write(json.dumps(log_entry) + '\n')
```

## Security Considerations

### Network Security

- The local AI server should only be accessible from the local network
- Use firewall rules to restrict access
- Consider using HTTPS for communication

### Data Privacy

- All data remains on your local network
- No data is sent to external services
- Consider encrypting sensitive data

## References

- [LocalAI Documentation](https://localai.io/basics/)
- [ComfyUI Documentation](https://github.com/comfyanonymous/ComfyUI/wiki)
- [Ollama Documentation](https://github.com/ollama/ollama/blob/main/README.md)
- [Stable Diffusion Documentation](https://huggingface.co/docs/diffusers/main/en/stable_diffusion) 