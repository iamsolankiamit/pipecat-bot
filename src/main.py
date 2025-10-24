"""
World of Doors - Pipecat Voice Bot
Main FastAPI application for handling Twilio webhook and bot spawning
"""

import os
import asyncio
from typing import Dict
from contextlib import asynccontextmanager
import aiohttp
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import Response, PlainTextResponse
from dotenv import load_dotenv
from loguru import logger
from twilio.twiml.voice_response import VoiceResponse
from pipecat.runner.daily import configure

from src.bot import WorldOfDoorsBot
from src.logging_config import setup_logging

# Load environment variables
load_dotenv()

# Setup logging
setup_logging()
logger.info("🚀 World of Doors Bot starting...")


# Initialize FastAPI app with aiohttp session
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create aiohttp session for Daily API calls
    app.state.session = aiohttp.ClientSession()
    yield
    # Close session when shutting down
    await app.state.session.close()


app = FastAPI(
    title="World of Doors Voice Bot",
    description="AI-powered voice assistant for appointment management",
    version="1.0.0",
    lifespan=lifespan
)

# Store active bot sessions
active_bots: Dict[str, WorldOfDoorsBot] = {}


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "World of Doors Voice Bot",
        "active_sessions": len(active_bots)
    }


@app.get("/health")
async def health():
    """Detailed health check"""
    return {
        "status": "healthy",
        "environment": os.getenv("ENVIRONMENT", "development"),
        "daily_configured": bool(os.getenv("DAILY_API_KEY")),
        "nestjs_api": os.getenv("NESTJS_API_URL"),
        "active_bots": len(active_bots)
    }


@app.post("/inbound-call", response_class=PlainTextResponse)
async def handle_inbound_call(request: Request):
    """
    Twilio webhook endpoint for incoming calls
    Creates a Daily room with SIP and spawns a bot
    """
    logger.debug("Received call webhook from Twilio")

    try:
        # Get form data from Twilio webhook
        form_data = await request.form()
        data = dict(form_data)

        # Extract call details
        call_sid = data.get("CallSid")
        if not call_sid:
            raise HTTPException(status_code=400, detail="Missing CallSid in request")

        caller_phone = str(data.get("From", "unknown-caller"))
        logger.info(f"Incoming call from {caller_phone}, CallSid: {call_sid}")

        # Check if we already have an active bot for this call
        if call_sid in active_bots:
            logger.info(f"Bot already exists for call {call_sid}, skipping duplicate")
            resp = VoiceResponse()
            return str(resp)

        # Create a Daily room with SIP capabilities using Pipecat's configure function
        try:
            sip_config = await configure(
                request.app.state.session,
                sip_caller_phone=caller_phone
            )
        except Exception as e:
            logger.error(f"Error creating Daily room: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to create Daily room: {str(e)}")

        # Extract details from SIP config
        room_url = sip_config.room_url
        token = sip_config.token
        sip_endpoint = sip_config.sip_endpoint

        # Make sure we have a SIP endpoint
        if not sip_endpoint:
            raise HTTPException(status_code=500, detail="No SIP endpoint provided by Daily")

        logger.info(f"✓ Created Daily room with SIP endpoint: {sip_endpoint}")

        # Extract room name from URL for tracking
        room_name = room_url.split("/")[-1]

        # Spawn bot in background task
        if call_sid not in active_bots:
            asyncio.create_task(
                spawn_bot(
                    call_sid=call_sid,
                    from_number=caller_phone,
                    room_url=room_url,
                    room_name=room_name,
                    token=token
                )
            )

        # Generate TwiML response to connect caller via SIP
        resp = VoiceResponse()
        
        resp.say("Please hold...", voice="alice")
        # Connect to Daily room via SIP (no "please hold" message)
        dial = resp.dial()
        dial.sip(sip_endpoint)

        twiml = str(resp)
        logger.debug(f"TwiML response: {twiml}")

        return twiml

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        resp = VoiceResponse()
        resp.say(
            "We're sorry, but we're experiencing technical difficulties. Please try again later.",
            voice="alice"
        )
        return str(resp)


async def spawn_bot(
    call_sid: str,
    from_number: str,
    room_url: str,
    room_name: str,
    token: str = None
):
    """
    Spawn a Pipecat bot to handle the conversation
    """
    logger.info(f"Spawning bot for call {call_sid}")

    try:
        # Create bot instance
        bot = WorldOfDoorsBot(
            call_sid=call_sid,
            caller_phone=from_number,
            room_url=room_url,
            room_name=room_name,
            token=token
        )

        # Store bot reference
        active_bots[call_sid] = bot

        # Run the bot (this will block until call ends)
        await bot.run()

        logger.info(f"Bot session ended for call {call_sid}")

    except Exception as e:
        logger.error(f"Error in bot session {call_sid}: {e}")

    finally:
        # Clean up bot reference
        if call_sid in active_bots:
            del active_bots[call_sid]

        logger.info(f"Cleaned up bot session for call {call_sid}")


@app.post("/end-call/{call_sid}")
async def end_call(call_sid: str):
    """
    Endpoint to manually end a call session
    """
    if call_sid in active_bots:
        bot = active_bots[call_sid]
        await bot.cleanup()
        return {"status": "call ended", "call_sid": call_sid}

    return {"status": "call not found", "call_sid": call_sid}


@app.get("/active-calls")
async def get_active_calls():
    """
    Get list of active call sessions
    """
    return {
        "active_calls": list(active_bots.keys()),
        "count": len(active_bots)
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 8000))
    env = os.getenv("ENVIRONMENT", "development")

    logger.info(f"Starting World of Doors Voice Bot on port {port} in {env} mode")

    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=port,
        reload=(env == "development")
    )
