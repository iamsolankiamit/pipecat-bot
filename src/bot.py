"""
World of Doors Bot - Main bot class
"""

from email import message
import os
from typing import Optional
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.transports.daily.transport import DailyTransport, DailyParams
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

from pipecat_flows import FlowManager

from src.services.api_client import WorldOfDoorsAPIClient
from src.flows.world_of_doors_flow import create_initial_node, set_api_client, set_task, initialize_caller_context


class WorldOfDoorsBot:
    """
    Main bot class that handles voice conversations for appointment management
    """

    def __init__(
        self,
        call_sid: str,
        caller_phone: str,
        room_url: str,
        room_name: str,
        token: str = None
    ):
        self.call_sid = call_sid
        self.caller_phone = caller_phone
        self.room_url = room_url
        self.room_name = room_name
        self.token = token

        # Session state
        self.state = {
            "call_sid": call_sid,
            "caller_phone": caller_phone,
            "contact": None,
            "appointment": None,
            "service_type": None,
            "available_slots": [],
            "selected_slot": None,
            "customer_name": None,
            "customer_email": None,
            "confirmation_number": None,
            "outcome": "NO_RESPONSE"  # Track call outcome
        }

        # API client
        self.api_client = WorldOfDoorsAPIClient()

        # Pipecat components (initialized in setup)
        self.transport: Optional[DailyTransport] = None
        self.pipeline: Optional[Pipeline] = None
        self.runner: Optional[PipelineRunner] = None

        logger.info(f"Bot initialized for call {call_sid} from {caller_phone}")

    async def setup(self):
        """Setup Pipecat pipeline with FlowManager for conversation flow"""

        logger.info(f"Setting up bot pipeline for room {self.room_name}")

        # Initialize transport (Daily.co)
        self.transport = DailyTransport(
            self.room_url,
            self.token,
            "World of Doors Assistant",
            DailyParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                camera_out_enabled=False,
                vad_analyzer=SileroVADAnalyzer(),
                transcription_enabled=False
            )
        )

        # Initialize STT (Speech-to-Text) - Deepgram
        stt_service = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY"),
            model="nova-2-phonecall",
            language="en-US"
        )

        # Initialize TTS (Text-to-Speech) - ElevenLabs
        tts_service = ElevenLabsTTSService(
            api_key=os.getenv("ELEVENLABS_API_KEY"),
            voice_id=os.getenv("ELEVENLABS_VOICE_ID", "s3TPKV1kjDlVtZbl4Ksh"),
            text_filters=[MarkdownTextFilter()]
        )

        # Initialize LLM - Anthropic Claude
        llm_service = AnthropicLLMService(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            model="claude-haiku-4-5-20251001"
        )

        # Create LLM context and aggregator
        context = LLMContext()
        context_aggregator = LLMContextAggregatorPair(context)

        # Build pipeline
        self.pipeline = Pipeline([
            self.transport.input(),
            stt_service,
            context_aggregator.user(),
            llm_service,
            tts_service,
            self.transport.output(),
            context_aggregator.assistant(),
        ])

        # Create task with interruptions enabled
        task = PipelineTask(
            self.pipeline,
            params=PipelineParams(allow_interruptions=True)
        )

        # Create runner
        self.runner = PipelineRunner()

        # Initialize FlowManager in dynamic mode
        flow_manager = FlowManager(
            task=task,
            llm=llm_service,
            context_aggregator=context_aggregator,
            transport=self.transport,
        )

        # Store flow_manager for later use
        self.flow_manager = flow_manager

        # Set the API client and task for use in flow handlers
        set_api_client(self.api_client)
        set_task(task)

        # Set up event handler for when participant joins
        @self.transport.event_handler("on_participant_joined")
        async def on_participant_joined(transport, participant):
            participant_id = participant.get("id")
            logger.info(f"Participant {participant_id} joined - initializing flow")

            # Initialize caller context and lookup contact
            await initialize_caller_context(self.caller_phone)

            # Initialize the conversation flow with the initial node
            # Set wait_for_user=False to have bot greet first
            await flow_manager.initialize(create_initial_node(wait_for_user=False))

        @self.transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Client disconnected")
            await task.cancel()

        logger.info("Bot pipeline setup complete with FlowManager")

        return task

    async def run(self):
        """Run the bot session"""

        logger.info(f"Starting bot session for call {self.call_sid}")

        try:
            # Setup pipeline
            task = await self.setup()

            # Lookup existing contact
            await self._lookup_contact()

            # Run the pipeline
            await self.runner.run(task)

            logger.info(f"Bot session completed for call {self.call_sid}")

        except Exception as e:
            logger.error(f"Error in bot session: {e}")
            raise

        finally:
            await self.cleanup()

    async def _lookup_contact(self):
        """Lookup contact by phone number"""
        try:
            contact = await self.api_client.lookup_contact(self.caller_phone)
            if contact:
                self.state["contact"] = contact
                logger.info(f"Found existing contact: {contact['firstName']} {contact['lastName']}")
            else:
                logger.info(f"No existing contact found for {self.caller_phone}")
        except Exception as e:
            logger.error(f"Error looking up contact: {e}")

    def determine_outcome(self) -> str:
        """
        Determine the outcome of the call

        Returns:
            One of: BOOKED, RESCHEDULED, CANCELLED, NO_RESPONSE, NOT_INTERESTED
        """
        if self.state.get("confirmation_number"):
            return "BOOKED"
        elif self.state.get("appointment"):
            return "RESCHEDULED"
        else:
            return self.state.get("outcome", "NO_RESPONSE")

    async def cleanup(self):
        """Cleanup bot resources"""

        logger.info(f"Cleaning up bot session for call {self.call_sid}")

        # Determine call outcome
        outcome = self.determine_outcome()
        self.state["outcome"] = outcome

        logger.info(f"Call outcome: {outcome}")

        # Close API client
        await self.api_client.close()

        # Close transport (Pipecat 0.0.91 uses cleanup instead of stop)
        if self.transport:
            try:
                await self.transport.cleanup()
            except Exception as e:
                logger.warning(f"Error cleaning up transport: {e}")

        logger.info(f"Bot cleanup complete for call {self.call_sid}")
