#!/usr/bin/env python3
"""
Number Lookup CLI Tool
Usage: python main.py <number>
"""

import sys
import requests
import re
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

def to_numbers(hex_string):
    """Convert hex string to bytes"""
    return bytes.fromhex(hex_string)

def decrypt_aes(ciphertext, key, iv):
    """Decrypt AES encrypted data"""
    cipher = AES.new(key, AES.MODE_CBC, iv)
    decrypted = cipher.decrypt(ciphertext)
    # Remove PKCS7 padding
    try:
        return unpad(decrypted, AES.block_size)
    except:
        return decrypted

def extract_cookie_params(html):
    """Extract encryption parameters from HTML"""
    # Extract all hex strings from JavaScript
    matches = re.findall(r'toNumbers\("([a-f0-9]+)"\)', html)
    
    if len(matches) < 3:
        return None
    
    # First three matches are a, b, c
    a = matches[0]
    b = matches[1]
    c = matches[2]
    
    return a, b, c

def get_cookie_value(html):
    """Decrypt and get the cookie value"""
    params = extract_cookie_params(html)
    if not params:
        return None
    
    a, b, c = params
    key = to_numbers(a)
    iv = to_numbers(b)
    ciphertext = to_numbers(c)
    
    decrypted = decrypt_aes(ciphertext, key, iv)
    return decrypted.hex()

def lookup_number(number):
    """
    Lookup number information using the API
    """
    base_url = f"https://lookup.42web.io/pkinfo.php?q={number}"
    
    # Browser-like headers
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
    }
    
    try:
        print(f"Looking up: {number}...")
        
        # Create a session to maintain cookies
        session = requests.Session()
        
        # First request - get the challenge
        response = session.get(base_url, headers=headers, timeout=10)
        
        # Check if we got the JavaScript challenge
        if '<script type="text/javascript" src="/aes.js"' in response.text:
            print("Handling cookie challenge...")
            
            # Extract and decrypt the cookie value
            cookie_value = get_cookie_value(response.text)
            if not cookie_value:
                print("Error: Failed to extract cookie parameters")
                return False
            
            # Set the cookie
            session.cookies.set('__test', cookie_value, domain='lookup.42web.io', path='/')
            
            # Make the second request with the cookie and i=1 parameter
            final_url = f"{base_url}&i=1"
            response = session.get(final_url, headers=headers, timeout=10)
        
        response.raise_for_status()
        
        # Display results
        print("\n" + "="*50)
        print("LOOKUP RESULTS")
        print("="*50)
        
        # Try to parse as JSON
        try:
            import json
            data = response.json()
            print(json.dumps(data, indent=2))
        except:
            # Print raw response
            print(response.text)
        
        return True
        
    except Exception as e:
        print(f"\nError: {str(e)}")
        return False

def main():
    if len(sys.argv) != 2:
        print("Usage: python main.py <number>")
        print("Example: python main.py 4230199577600")
        sys.exit(1)
    
    number = sys.argv[1].strip()
    
    if not number:
        print("Error: Please provide a valid number")
        sys.exit(1)
    
    success = lookup_number(number)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()