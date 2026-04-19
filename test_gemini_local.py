#!/usr/bin/env python3
"""
Local test script to verify Gemini integration is working.

Run this BEFORE deploying to verify:
1. New SDK is installed correctly
2. API key works
3. API calls succeed
4. Output varies across runs
5. ai_powered returns True

Usage:
    python test_gemini_local.py
"""

import os
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

def test_gemini_integration():
    """Test Gemini integration locally."""
    
    print("=" * 80)
    print("🔬 GEMINI INTEGRATION TEST")
    print("=" * 80)
    print()
    
    # Check environment
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY not set in environment")
        print("   Set it with: export GEMINI_API_KEY=your-key-here")
        return False
    
    print(f"✓ GEMINI_API_KEY is set ({api_key[:20]}...)")
    print()
    
    # Test SDK import
    try:
        from google import genai
        from google.genai import types
        print("✓ google-genai SDK imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import google-genai: {e}")
        print("   Install with: pip install google-genai==0.2.2")
        return False
    print()
    
    # Test client creation
    try:
        client = genai.Client(api_key=api_key)
        print("✓ Gemini client created successfully")
    except Exception as e:
        print(f"❌ Failed to create client: {e}")
        return False
    print()
    
    # Test API call
    print("🧪 Testing API call...")
    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents="Say 'Hello from SmartFlow AI' in exactly 5 words.",
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=50,
            )
        )
        
        if response and response.text:
            print(f"✓ API call successful!")
            print(f"  Response: {response.text.strip()}")
        else:
            print("❌ API call returned empty response")
            return False
    except Exception as e:
        print(f"❌ API call failed: {type(e).__name__}: {e}")
        return False
    print()
    
    # Test variation
    print("🧪 Testing output variation (3 runs)...")
    outputs = []
    
    for i in range(3):
        try:
            response = client.models.generate_content(
                model='gemini-1.5-flash',
                contents="Explain crowd congestion at a stadium gate in one sentence.",
                config=types.GenerateContentConfig(
                    temperature=0.7,
                    max_output_tokens=100,
                )
            )
            
            if response and response.text:
                output = response.text.strip()
                outputs.append(output)
                print(f"  Run {i+1}: {output[:80]}...")
            else:
                print(f"  Run {i+1}: Empty response")
        except Exception as e:
            print(f"  Run {i+1}: Error - {e}")
    
    print()
    
    # Check variation
    if len(set(outputs)) == len(outputs):
        print("✓ Output varies across runs (GOOD - Real AI)")
    elif len(set(outputs)) == 1:
        print("⚠️  Output is identical across runs (might be too deterministic)")
    else:
        print("✓ Some variation detected")
    print()
    
    # Test with app code
    print("🧪 Testing with app code...")
    try:
        from app.gemini_service import generate_crowd_analysis_insight
        from app.models import RiskLevel
        
        insight, ai_powered = generate_crowd_analysis_insight(
            zone="Gate A",
            risk_level=RiskLevel.HIGH,
            severity_score=85.0,
            confidence_score=90.0,
            predicted_congestion=True,
            contributing_factors=[
                "Very high crowd density at 85.0% capacity",
                "Long queue wait of 25 minutes",
                "Post Match phase driving simultaneous crowd movement",
            ],
            recommendation="High congestion risk at Gate A. Activate crowd management protocols.",
        )
        
        print(f"  ai_powered: {ai_powered}")
        print(f"  insight: {insight[:100]}...")
        
        if ai_powered:
            print("✓ App integration working - ai_powered is TRUE")
        else:
            print("❌ App integration using fallback - ai_powered is FALSE")
            return False
            
    except Exception as e:
        print(f"❌ App integration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print()
    print("=" * 80)
    print("✅ ALL TESTS PASSED - Gemini integration is working!")
    print("=" * 80)
    print()
    print("Next steps:")
    print("1. Commit and push code changes")
    print("2. Deploy to Cloud Run with GEMINI_API_KEY environment variable")
    print("3. Test production endpoint")
    print()
    
    return True


if __name__ == "__main__":
    success = test_gemini_integration()
    sys.exit(0 if success else 1)
