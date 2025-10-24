# Copyright (c) 2024-2025, World of Doors
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""World of Doors appointment scheduling flow using Pipecat Flows.

This implements a complete appointment management system with dynamic flows where
conversation paths are determined at runtime. The flow handles:

1. Greeting and intent detection (new, reschedule, cancel, product info)
2. Service type collection for new appointments
3. Customer information gathering
4. Calendar availability checking
5. Appointment confirmation and booking
6. Reschedule with 24-hour policy check
7. Cancellation with 24-hour policy check
8. Product information and objection handling

Based on the Pipecat-Flows dynamic restaurant reservation example.
"""

import asyncio
import os
from datetime import datetime, timedelta
from typing import Optional

from dotenv import load_dotenv
from loguru import logger

from pipecat_flows import FlowArgs, FlowManager, FlowResult, FlowsFunctionSchema, NodeConfig
from pipecat.frames.frames import EndFrame

load_dotenv(override=True)


# ============================================================================
# Type definitions for function results
# ============================================================================

class IntentResult(FlowResult):
    """Result from intent detection"""
    intent: str
    customer_name: Optional[str]
    phone_number: Optional[str]


class ServiceTypeResult(FlowResult):
    """Result from service type collection"""
    service_type: str
    issue_description: Optional[str]


class CustomerInfoResult(FlowResult):
    """Result from customer info collection"""
    customer_name: str
    phone_number: str
    email: Optional[str]
    service_address: str


class AvailabilityResult(FlowResult):
    """Result from availability check"""
    available: bool
    preferred_date: str
    preferred_time: str
    selected_datetime: Optional[str]
    alternative_times: Optional[list[str]]


class AppointmentResult(FlowResult):
    """Result from appointment booking"""
    booked: bool
    confirmation_number: Optional[str]
    appointment_time: str


class RescheduleCheckResult(FlowResult):
    """Result from reschedule policy check"""
    within_24_hours: bool
    current_appointment_time: str
    proceed: bool


class CancelCheckResult(FlowResult):
    """Result from cancel policy check"""
    within_24_hours: bool
    current_appointment_time: str
    decision: str  # "cancel", "reschedule", or "keep"


# ============================================================================
# Helper functions and global state
# ============================================================================

# Global API client and task instances (will be set by bot)
_api_client = None
_task = None
_flow_context = {}  # Store conversation data across nodes


def set_api_client(api_client):
    """Set the global API client for use in flow handlers"""
    global _api_client
    _api_client = api_client


def get_api_client():
    """Get the global API client"""
    return _api_client


def set_task(task):
    """Set the global task for use in flow handlers"""
    global _task
    _task = task


def get_task():
    """Get the global task"""
    return _task


def set_context(key: str, value):
    """Store data in flow context"""
    global _flow_context
    _flow_context[key] = value


def get_context(key: str, default=None):
    """Retrieve data from flow context"""
    return _flow_context.get(key, default)


def clear_context():
    """Clear flow context (call at end of conversation)"""
    global _flow_context
    _flow_context = {}


async def initialize_caller_context(caller_phone: str):
    """
    Initialize context with caller's phone and lookup existing contact.
    Call this when the flow starts.
    """
    set_context("caller_phone", caller_phone)
    logger.info(f"📞 Initializing context for caller: {caller_phone}")

    # Lookup existing contact
    contact = await lookup_or_create_contact(caller_phone)
    if contact:
        set_context("contact_id", contact.get("id"))
        set_context("existing_contact", contact)
        logger.info(f"✅ Found existing contact ID: {contact.get('id')}")
    else:
        logger.info("📝 New caller - will create contact when we collect info")


async def lookup_or_create_contact(phone: str) -> Optional[dict]:
    """
    Lookup existing contact by phone or return None if not found.
    We'll create the contact later when we have full details.
    """
    api_client = get_api_client()
    if not api_client:
        logger.warning("API client not available for contact lookup")
        return None

    try:
        logger.info(f"🔍 Looking up contact by phone: {phone}")
        contact = await api_client.lookup_contact(phone)
        if contact:
            logger.info(f"✅ Found existing contact: {contact.get('firstName')} {contact.get('lastName')}")
            return contact
        else:
            logger.info(f"📝 No existing contact found for {phone}")
            return None
    except Exception as e:
        logger.error(f"Error looking up contact: {e}")
        return None


async def create_or_update_contact(phone: str, name: str, email: Optional[str] = None, address: Optional[str] = None) -> Optional[dict]:
    """
    Create a new contact or update existing one with additional details.
    """
    api_client = get_api_client()
    if not api_client:
        logger.warning("API client not available for contact creation")
        return None

    try:
        # Check if contact already exists
        existing_contact = await api_client.lookup_contact(phone)

        if existing_contact:
            logger.info(f"♻️  Contact exists, using contact ID: {existing_contact.get('id')}")
            # TODO: Add update endpoint to NestJS API if we want to update contact details
            return existing_contact
        else:
            # Parse name into first and last
            name_parts = name.strip().split(maxsplit=1)
            first_name = name_parts[0] if len(name_parts) > 0 else name
            last_name = name_parts[1] if len(name_parts) > 1 else ""

            contact_data = {
                "firstName": first_name,
                "lastName": last_name,
                "phone": phone,
                "email": email,
                "address": address
            }

            logger.info(f"➕ Creating new contact: {first_name} {last_name}")
            contact = await api_client.create_contact(contact_data)

            if contact:
                logger.info(f"✅ Contact created with ID: {contact.get('id')}")
                return contact
            else:
                logger.error("Failed to create contact")
                return None

    except Exception as e:
        logger.error(f"Error creating/updating contact: {e}")
        return None


def calculate_hours_until(scheduled_time: str) -> float:
    """Calculate hours from now until the scheduled time"""
    try:
        appointment_time = datetime.fromisoformat(scheduled_time.replace('Z', '+00:00'))
        now = datetime.now(appointment_time.tzinfo)
        diff = appointment_time - now
        return diff.total_seconds() / 3600
    except Exception as e:
        logger.error(f"Error calculating hours until appointment: {e}")
        return 999  # Return a large number to avoid triggering 24h policy


# ============================================================================
# Function handlers
# ============================================================================

async def handle_new_appointment(args: FlowArgs) -> tuple[IntentResult, NodeConfig]:
    """Handle new appointment request"""
    logger.info("Customer wants to schedule new appointment")

    result = IntentResult(intent="new_appointment")
    next_node = create_service_type_node()

    return result, next_node


async def handle_reschedule_request(args: FlowArgs) -> tuple[IntentResult, NodeConfig]:
    """Handle reschedule request"""
    customer_name = args.get("customer_name")
    phone_number = args.get("phone_number")

    logger.info(f"Customer wants to reschedule: {customer_name}, {phone_number}")

    result = IntentResult(
        intent="reschedule",
        customer_name=customer_name,
        phone_number=phone_number
    )
    next_node = create_reschedule_lookup_node()

    return result, next_node


async def handle_cancel_request(args: FlowArgs) -> tuple[IntentResult, NodeConfig]:
    """Handle cancel request"""
    customer_name = args.get("customer_name")
    phone_number = args.get("phone_number")

    logger.info(f"Customer wants to cancel: {customer_name}, {phone_number}")

    result = IntentResult(
        intent="cancel",
        customer_name=customer_name,
        phone_number=phone_number
    )
    next_node = create_cancel_lookup_node()

    return result, next_node


async def handle_product_info_request(args: FlowArgs) -> tuple[IntentResult, NodeConfig]:
    """Handle product info request"""
    logger.info("Customer has questions about products/services")

    result = IntentResult(intent="product_info")
    next_node = create_product_info_node()

    return result, next_node


async def collect_service_type(args: FlowArgs) -> tuple[ServiceTypeResult, NodeConfig]:
    """Collect service type from customer"""
    logger.info("="*60)
    logger.info("🎯 FLOW HANDLER | collect_service_type called")
    logger.info("="*60)

    service_type = args["service_type"]
    issue_description = args.get("issue_description")

    logger.info(f"🔧 Service type collected: {service_type}")
    logger.info(f"📝 Issue description: {issue_description}")

    # Store in context for later use
    set_context("service_type", service_type)
    set_context("issue_description", issue_description)

    result = ServiceTypeResult(
        service_type=service_type,
        issue_description=issue_description
    )
    next_node = create_customer_info_node()

    return result, next_node


async def collect_customer_info(args: FlowArgs) -> tuple[CustomerInfoResult, NodeConfig]:
    """Collect customer information"""
    logger.info("="*60)
    logger.info("🎯 FLOW HANDLER | collect_customer_info called")
    logger.info("="*60)

    customer_name = args["customer_name"]
    phone_number = args["phone_number"]
    email = args.get("email")
    service_address = args["service_address"]

    logger.info(f"📋 Customer info collected:")
    logger.info(f"  - Name: {customer_name}")
    logger.info(f"  - Phone: {phone_number}")
    logger.info(f"  - Email: {email}")
    logger.info(f"  - Address: {service_address}")

    # Store in context for later use
    set_context("customer_name", customer_name)
    set_context("phone_number", phone_number)
    set_context("email", email)
    set_context("service_address", service_address)

    # Create or update contact in the database
    contact = await create_or_update_contact(
        phone=phone_number,
        name=customer_name,
        email=email,
        address=service_address
    )

    if contact:
        contact_id = contact.get("id")
        set_context("contact_id", contact_id)
        logger.info(f"✅ Contact ID stored: {contact_id}")
    else:
        logger.warning("⚠️  Failed to create/update contact, will use phone number instead")

    result = CustomerInfoResult(
        customer_name=customer_name,
        phone_number=phone_number,
        email=email,
        service_address=service_address
    )
    next_node = create_schedule_appointment_node()

    return result, next_node


async def check_availability_and_schedule(args: FlowArgs) -> tuple[AvailabilityResult, NodeConfig]:
    """Check calendar availability and schedule appointment"""
    logger.info("="*60)
    logger.info("🎯 FLOW HANDLER | check_availability_and_schedule called")
    logger.info("="*60)

    preferred_date = args["preferred_date"]
    preferred_time = args["preferred_time"]

    logger.info(f"📅 Checking availability for {preferred_date} at {preferred_time}")

    api_client = get_api_client()
    logger.info(f"API Client available: {api_client is not None}")

    if api_client:
        try:
            # Check availability with NestJS API
            availability_result = await api_client.check_availability(
                date=preferred_date,
                duration_hours=2
            )

            if availability_result and availability_result.get("available"):
                slots = availability_result.get("slots", [])

                if slots:
                    # Find a slot matching the preferred time or use the first available
                    selected_slot = None
                    for slot in slots:
                        slot_start = slot.get("start", "")
                        # Simple time matching - could be improved
                        if preferred_time in slot_start:
                            selected_slot = slot
                            break

                    if not selected_slot:
                        selected_slot = slots[0]

                    selected_datetime = selected_slot["start"]

                    # Store selected datetime in context for booking
                    set_context("selected_datetime", selected_datetime)

                    result = AvailabilityResult(
                        available=True,
                        preferred_date=preferred_date,
                        preferred_time=preferred_time,
                        selected_datetime=selected_datetime,
                        alternative_times=None
                    )
                    next_node = create_confirm_appointment_node()
                else:
                    # No slots available
                    alternative_times = ["9:00 AM", "10:00 AM", "2:00 PM", "3:00 PM"]
                    result = AvailabilityResult(
                        available=False,
                        preferred_date=preferred_date,
                        preferred_time=preferred_time,
                        selected_datetime=None,
                        alternative_times=alternative_times
                    )
                    next_node = create_no_availability_node(alternative_times)
            else:
                # No availability
                alternative_times = ["9:00 AM", "10:00 AM", "2:00 PM", "3:00 PM"]
                result = AvailabilityResult(
                    available=False,
                    preferred_date=preferred_date,
                    preferred_time=preferred_time,
                    selected_datetime=None,
                    alternative_times=alternative_times
                )
                next_node = create_no_availability_node(alternative_times)

            return result, next_node

        except Exception as e:
            logger.error(f"Error checking availability: {e}")

    # Fallback to mock data if API not available
    logger.warning("API client not available, using mock availability data")

    # Mock: all times available except 7:00 PM and 8:00 PM
    booked_times = {"7:00 PM", "8:00 PM", "19:00", "20:00"}
    is_available = preferred_time not in booked_times

    if is_available:
        selected_datetime = f"{preferred_date}T{preferred_time.replace(' ', '')}:00"

        # Store selected datetime in context for booking
        set_context("selected_datetime", selected_datetime)

        result = AvailabilityResult(
            available=True,
            preferred_date=preferred_date,
            preferred_time=preferred_time,
            selected_datetime=selected_datetime,
            alternative_times=None
        )
        next_node = create_confirm_appointment_node()
    else:
        alternative_times = ["9:00 AM", "10:00 AM", "2:00 PM", "3:00 PM", "5:00 PM"]

        result = AvailabilityResult(
            available=False,
            preferred_date=preferred_date,
            preferred_time=preferred_time,
            selected_datetime=None,
            alternative_times=alternative_times
        )
        next_node = create_no_availability_node(alternative_times)

    return result, next_node


async def confirm_and_book_appointment(args: FlowArgs) -> tuple[AppointmentResult, NodeConfig]:
    """Confirm booking and create appointment"""
    logger.info("="*60)
    logger.info("🎯 FLOW HANDLER | confirm_and_book_appointment called")
    logger.info("="*60)

    api_client = get_api_client()
    logger.info(f"API Client available: {api_client is not None}")

    # Get all data from context
    service_type = get_context("service_type", "REPAIR")
    issue_description = get_context("issue_description")
    customer_name = get_context("customer_name")
    phone_number = get_context("phone_number")
    email = get_context("email")
    service_address = get_context("service_address")
    selected_datetime = get_context("selected_datetime")
    contact_id = get_context("contact_id")  # Get contact ID if available

    logger.info(f"📋 Context Data:")
    logger.info(f"  - Service Type: {service_type}")
    logger.info(f"  - Customer: {customer_name}")
    logger.info(f"  - Phone: {phone_number}")
    logger.info(f"  - Email: {email}")
    logger.info(f"  - Address: {service_address}")
    logger.info(f"  - DateTime: {selected_datetime}")
    logger.info(f"  - Contact ID: {contact_id}")

    if not all([customer_name, phone_number, selected_datetime]):
        logger.error("❌ Missing required appointment data in context")
        logger.error(f"  - customer_name: {customer_name}")
        logger.error(f"  - phone_number: {phone_number}")
        logger.error(f"  - selected_datetime: {selected_datetime}")
        return AppointmentResult(
            booked=False,
            confirmation_number=None,
            appointment_time="TBD"
        ), create_schedule_appointment_node()

    # Calculate end time (2 hours after start)
    start_time = datetime.fromisoformat(selected_datetime.replace('Z', '+00:00'))
    end_time = start_time + timedelta(hours=2)

    logger.info(f"Creating appointment: {customer_name} at {selected_datetime}")

    if api_client:
        try:
            # Create appointment via NestJS API
            # Use contactId if available, otherwise use phone/name
            appointment_data = {
                "scheduledTime": start_time.isoformat(),
                "endTime": end_time.isoformat(),
                "serviceType": service_type.upper(),
                "issueDescription": issue_description
            }

            # Add contact ID or customer details
            if contact_id:
                appointment_data["contactId"] = contact_id
                logger.info(f"🔗 Linking appointment to contact ID: {contact_id}")
            else:
                appointment_data["customerPhone"] = phone_number
                appointment_data["customerName"] = customer_name
                appointment_data["customerEmail"] = email
                logger.info(f"📝 Creating appointment with customer details (no contact ID)")

            appointment = await api_client.create_appointment(appointment_data)

            if appointment:
                confirmation_number = appointment.get("confirmationNumber")
                logger.info(f"✓ Appointment created: {confirmation_number}")

                # Store for later reference
                set_context("appointment_id", appointment.get("id"))
                set_context("confirmation_number", confirmation_number)

                result = AppointmentResult(
                    booked=True,
                    confirmation_number=confirmation_number,
                    appointment_time=selected_datetime
                )
                next_node = create_appointment_confirmed_node()

                return result, next_node
            else:
                logger.error("Failed to create appointment - no response from API")

        except Exception as e:
            logger.error(f"Error creating appointment via API: {e}")

    # Fallback to mock data if API fails
    logger.warning("Using mock appointment data")
    confirmation_number = f"WOD{datetime.now().strftime('%Y%m%d%H%M%S')}"

    set_context("confirmation_number", confirmation_number)

    result = AppointmentResult(
        booked=True,
        confirmation_number=confirmation_number,
        appointment_time=selected_datetime or "TBD"
    )
    next_node = create_appointment_confirmed_node()

    return result, next_node


async def lookup_and_check_reschedule(args: FlowArgs) -> tuple[RescheduleCheckResult, NodeConfig]:
    """Look up appointment and check reschedule policy"""
    customer_name = args.get("customer_name")
    phone_number = args.get("phone_number")

    logger.info(f"Looking up appointment for reschedule: {customer_name}, {phone_number}")

    api_client = get_api_client()

    # Store customer info for later
    set_context("lookup_name", customer_name)
    set_context("lookup_phone", phone_number)

    # For now, we'll ask them to provide confirmation number in the prompt
    # TODO: Add phone-based lookup to NestJS API
    # In real implementation, we would:
    # 1. Look up contact by phone
    # 2. Find their most recent scheduled appointment
    # For now, use mock data that prompts for confirmation number

    logger.warning("Phone-based appointment lookup not yet implemented in API")
    logger.info("Customer will need to provide confirmation number")

    # Mock appointment data
    current_appointment_time = (datetime.now() + timedelta(days=2)).isoformat()
    hours_until = calculate_hours_until(current_appointment_time)
    within_24h = hours_until < 24

    result = RescheduleCheckResult(
        within_24_hours=within_24h,
        current_appointment_time=current_appointment_time,
        proceed=True
    )
    next_node = create_reschedule_new_time_node()

    return result, next_node


async def reschedule_to_new_time(args: FlowArgs) -> tuple[AvailabilityResult, NodeConfig]:
    """Reschedule appointment to new time"""
    new_datetime_str = args["new_datetime"]

    logger.info(f"Rescheduling to {new_datetime_str}")

    api_client = get_api_client()
    appointment_id = get_context("appointment_id")

    # Parse new datetime
    try:
        new_start = datetime.fromisoformat(new_datetime_str.replace('Z', '+00:00'))
        new_end = new_start + timedelta(hours=2)
    except Exception as e:
        logger.error(f"Error parsing datetime: {e}")
        return AvailabilityResult(
            available=False,
            preferred_date="",
            preferred_time="",
            selected_datetime=None,
            alternative_times=["9:00 AM", "11:00 AM", "2:00 PM"]
        ), create_reschedule_new_time_node()

    if api_client and appointment_id:
        try:
            # Update appointment via NestJS API
            updated = await api_client.update_appointment(
                appointment_id,
                {
                    "scheduledTime": new_start.isoformat(),
                    "endTime": new_end.isoformat()
                }
            )

            if updated:
                logger.info(f"✓ Appointment rescheduled to {new_datetime_str}")
                set_context("selected_datetime", new_datetime_str)

                result = AvailabilityResult(
                    available=True,
                    preferred_date=new_start.strftime("%Y-%m-%d"),
                    preferred_time=new_start.strftime("%I:%M %p"),
                    selected_datetime=new_datetime_str,
                    alternative_times=None
                )
                next_node = create_appointment_confirmed_node()

                return result, next_node
            else:
                logger.error("Failed to reschedule appointment")

        except Exception as e:
            logger.error(f"Error rescheduling appointment: {e}")

    # Fallback - use mock data
    logger.warning("Using mock reschedule (API not available)")
    set_context("selected_datetime", new_datetime_str)

    result = AvailabilityResult(
        available=True,
        preferred_date=new_datetime_str.split('T')[0] if 'T' in new_datetime_str else "",
        preferred_time="",
        selected_datetime=new_datetime_str,
        alternative_times=None
    )
    next_node = create_appointment_confirmed_node()

    return result, next_node


async def lookup_and_check_cancel(args: FlowArgs) -> tuple[CancelCheckResult, NodeConfig]:
    """Look up appointment and check cancel policy"""
    customer_name = args.get("customer_name")
    phone_number = args.get("phone_number")

    logger.info(f"Looking up appointment for cancel: {customer_name}, {phone_number}")

    api_client = get_api_client()

    # Store customer info
    set_context("lookup_name", customer_name)
    set_context("lookup_phone", phone_number)

    # Similar to reschedule, we need phone-based lookup
    # TODO: Add to NestJS API
    logger.warning("Phone-based appointment lookup not yet implemented in API")

    # Mock appointment data
    current_appointment_time = (datetime.now() + timedelta(days=2)).isoformat()
    hours_until = calculate_hours_until(current_appointment_time)
    within_24h = hours_until < 24

    result = CancelCheckResult(
        within_24_hours=within_24h,
        current_appointment_time=current_appointment_time,
        decision="pending"
    )
    next_node = create_cancel_decision_node(within_24h, current_appointment_time)

    return result, next_node


async def proceed_with_cancellation(args: FlowArgs) -> tuple[None, NodeConfig]:
    """Cancel the appointment"""
    logger.info("Proceeding with cancellation")

    api_client = get_api_client()
    appointment_id = get_context("appointment_id")

    if api_client and appointment_id:
        try:
            # Delete appointment via NestJS API
            result = await api_client.cancel_appointment(appointment_id)

            if result:
                logger.info(f"✓ Appointment cancelled: {appointment_id}")
            else:
                logger.error("Failed to cancel appointment")

        except Exception as e:
            logger.error(f"Error cancelling appointment: {e}")
    else:
        logger.warning("Using mock cancellation (API not available or no appointment_id)")

    next_node = create_cancellation_confirmed_node()

    return None, next_node


async def keep_appointment(args: FlowArgs) -> tuple[None, NodeConfig]:
    """Customer decides to keep appointment"""
    logger.info("Customer decided to keep appointment")

    next_node = create_appointment_confirmed_node()

    return None, next_node


async def handle_product_inquiry(args: FlowArgs) -> tuple[IntentResult, NodeConfig]:
    """Handle what customer wants to do after product info"""
    next_action = args["next_action"]

    logger.info(f"After product info, customer wants to: {next_action}")

    if next_action == "schedule":
        next_node = create_service_type_node()
    elif next_action == "more_questions":
        next_node = create_product_info_node()
    else:  # done
        next_node = create_end_node()

    result = IntentResult(intent=next_action)
    return result, next_node


async def end_conversation(args: FlowArgs) -> tuple[None, NodeConfig]:
    """End the conversation"""
    logger.info("Ending conversation - queuing EndFrame")

    # Clear flow context
    clear_context()
    logger.info("Flow context cleared")

    # Queue an EndFrame to end the pipeline and close the call
    task = get_task()
    if task:
        await task.queue_frame(EndFrame())
        logger.info("EndFrame queued successfully")
    else:
        logger.warning("No task available to queue EndFrame")

    return None, None


# ============================================================================
# Function schemas
# ============================================================================

new_appointment_schema = FlowsFunctionSchema(
    name="new_appointment",
    description="Customer wants to schedule a new appointment",
    properties={},
    required=[],
    handler=handle_new_appointment,
)

reschedule_request_schema = FlowsFunctionSchema(
    name="reschedule_appointment",
    description="Customer wants to reschedule an existing appointment",
    properties={
        "customer_name": {
            "type": "string",
            "description": "Customer's name for lookup"
        },
        "phone_number": {
            "type": "string",
            "description": "Customer's phone number for lookup"
        }
    },
    required=["customer_name", "phone_number"],
    handler=handle_reschedule_request,
)

cancel_request_schema = FlowsFunctionSchema(
    name="cancel_appointment",
    description="Customer wants to cancel an appointment",
    properties={
        "customer_name": {
            "type": "string",
            "description": "Customer's name for lookup"
        },
        "phone_number": {
            "type": "string",
            "description": "Customer's phone number for lookup"
        }
    },
    required=["customer_name", "phone_number"],
    handler=handle_cancel_request,
)

product_info_request_schema = FlowsFunctionSchema(
    name="product_info",
    description="Customer has questions about products or services",
    properties={},
    required=[],
    handler=handle_product_info_request,
)

service_type_schema = FlowsFunctionSchema(
    name="collect_service_type",
    description="Call this IMMEDIATELY after customer tells you what service they need. This saves their service type and moves to the next step.",
    properties={
        "service_type": {
            "type": "string",
            "enum": ["repair", "installation", "maintenance", "inspection"],
            "description": "Type of garage door service needed"
        },
        "issue_description": {
            "type": "string",
            "description": "Brief description of the garage door issue"
        }
    },
    required=["service_type"],
    handler=collect_service_type,
)

customer_info_schema = FlowsFunctionSchema(
    name="collect_customer_info",
    description="Call this IMMEDIATELY after collecting customer's name, phone, and address. This saves their information and moves to scheduling.",
    properties={
        "customer_name": {
            "type": "string",
            "description": "Customer's full name"
        },
        "phone_number": {
            "type": "string",
            "description": "Customer's contact phone number"
        },
        "email": {
            "type": "string",
            "description": "Customer's email address (optional)"
        },
        "service_address": {
            "type": "string",
            "description": "Address where service is needed"
        }
    },
    required=["customer_name", "phone_number", "service_address"],
    handler=collect_customer_info,
)

availability_schema = FlowsFunctionSchema(
    name="check_availability",
    description="Call this IMMEDIATELY after customer tells you their preferred date and time. This checks calendar availability. Do not wait or say anything else - call this function right away.",
    properties={
        "preferred_date": {
            "type": "string",
            "description": "Preferred date in YYYY-MM-DD format (e.g., '2025-10-25'). Convert relative dates like 'tomorrow' or 'next Monday' to this format."
        },
        "preferred_time": {
            "type": "string",
            "description": "Preferred time (e.g., '2:00 PM', '14:00', '10 AM'). Must be between 9 AM and 6 PM."
        }
    },
    required=["preferred_date", "preferred_time"],
    handler=check_availability_and_schedule,
)

confirm_booking_schema = FlowsFunctionSchema(
    name="confirm_booking",
    description="Call this IMMEDIATELY when customer confirms appointment is correct (says yes, looks good, that's right, etc). This creates the appointment in the system.",
    properties={
        "appointment_time": {
            "type": "string",
            "description": "The confirmed appointment date and time"
        }
    },
    required=["appointment_time"],
    handler=confirm_and_book_appointment,
)

reschedule_lookup_schema = FlowsFunctionSchema(
    name="lookup_reschedule",
    description="Look up appointment and check if rescheduling is allowed",
    properties={
        "customer_name": {
            "type": "string",
            "description": "Customer's name"
        },
        "phone_number": {
            "type": "string",
            "description": "Customer's phone number"
        }
    },
    required=["customer_name", "phone_number"],
    handler=lookup_and_check_reschedule,
)

reschedule_execute_schema = FlowsFunctionSchema(
    name="reschedule_to_new_time",
    description="Reschedule appointment to new date/time",
    properties={
        "new_datetime": {
            "type": "string",
            "description": "New appointment date and time"
        }
    },
    required=["new_datetime"],
    handler=reschedule_to_new_time,
)

cancel_lookup_schema = FlowsFunctionSchema(
    name="lookup_cancel",
    description="Look up appointment for cancellation",
    properties={
        "customer_name": {
            "type": "string",
            "description": "Customer's name"
        },
        "phone_number": {
            "type": "string",
            "description": "Customer's phone number"
        }
    },
    required=["customer_name", "phone_number"],
    handler=lookup_and_check_cancel,
)

proceed_cancel_schema = FlowsFunctionSchema(
    name="proceed_with_cancel",
    description="Customer confirms they want to cancel",
    properties={},
    required=[],
    handler=proceed_with_cancellation,
)

keep_appointment_schema = FlowsFunctionSchema(
    name="keep_appointment",
    description="Customer decides to keep their appointment",
    properties={},
    required=[],
    handler=keep_appointment,
)

product_inquiry_schema = FlowsFunctionSchema(
    name="product_inquiry_action",
    description="What customer wants to do after getting product info",
    properties={
        "next_action": {
            "type": "string",
            "enum": ["schedule", "more_questions", "done"],
            "description": "Customer's next desired action"
        }
    },
    required=["next_action"],
    handler=handle_product_inquiry,
)

end_conversation_schema = FlowsFunctionSchema(
    name="end_conversation",
    description="End the conversation",
    properties={},
    required=[],
    handler=end_conversation,
)


# ============================================================================
# Node configurations
# ============================================================================

def create_initial_node(wait_for_user: bool = False) -> NodeConfig:
    """Create initial greeting node for intent detection"""
    # Get current date/time information
    now = datetime.now()
    current_date = now.strftime("%A, %B %d, %Y")  # e.g., "Monday, October 22, 2025"
    current_time = now.strftime("%I:%M %p")  # e.g., "02:30 PM"

    return {
        "name": "start",
        "role_messages": [
            {
                "role": "system",
                "content": f"""You are Jordan, an inbound customer service representative for World of Doors, a garage door service company.

IMPORTANT CONTEXT:
- Today's date is: {current_date}
- Current time is: {current_time}
- Use this information when discussing appointment dates and times
- When customer says "tomorrow", you know what date that is
- When customer says "next week", you can calculate the dates

Speak clearly, professionally, and naturally. Use contractions and be conversational, but avoid excessive filler words like "um", "uh", "oh", "like", "awesome" - use these sparingly only when they add naturalness. Stay friendly but efficient.

This is a voice conversation, so avoid special characters, emojis, and overly formal language."""
            }
        ],
        "task_messages": [
            {
                "role": "system",
                "content": """Warmly greet the customer and ask how you can help them today.

Listen for:
- New appointment scheduling
- Rescheduling an existing appointment
- Cancelling an appointment
- Questions about products/services

Example greeting: "Hey! Thanks for calling World of Doors, this is Jordan. How can I help you today?"

Keep it brief and natural."""
            }
        ],
        "functions": [
            new_appointment_schema,
            reschedule_request_schema,
            cancel_request_schema,
            product_info_request_schema,
        ],
        "respond_immediately": not wait_for_user,
    }


def create_service_type_node() -> NodeConfig:
    """Create node for collecting service type"""
    return {
        "name": "service_type",
        "task_messages": [
            {
                "role": "system",
                "content": """Ask briefly what's going on with their garage door.

Example: "Okay, what's going on with the door?"

Once they describe the issue, call collect_service_type with the details."""
            }
        ],
        "functions": [service_type_schema],
    }


def create_customer_info_node() -> NodeConfig:
    """Create node for collecting customer information"""
    return {
        "name": "customer_info",
        "task_messages": [
            {
                "role": "system",
                "content": """Get name, phone, email (optional), and service address efficiently.

Example: "Great. Can I get your name, phone number, and the service address?"

Call collect_customer_info once you have the details."""
            }
        ],
        "functions": [customer_info_schema],
    }


def create_schedule_appointment_node() -> NodeConfig:
    """Create node for scheduling appointment"""
    now = datetime.now()
    current_date = now.strftime("%A, %B %d, %Y")
    tomorrow = (now + timedelta(days=1)).strftime("%A, %B %d, %Y")

    return {
        "name": "schedule_appointment",
        "task_messages": [
            {
                "role": "system",
                "content": f"""Ask when they'd like to schedule.

Today is: {current_date}
Tomorrow is: {tomorrow}
Hours: 9 AM - 6 PM, Mon-Sat

Example: "When works best for you?"

Call check_availability with their preferred date/time (format: YYYY-MM-DD, HH:MM)."""
            }
        ],
        "functions": [availability_schema],
    }


def create_confirm_appointment_node() -> NodeConfig:
    """Create node for confirming appointment"""
    return {
        "name": "confirm_appointment",
        "task_messages": [
            {
                "role": "system",
                "content": """Quickly confirm the key details:

Example: "Okay, so {{service_type}} on {{date}} at {{time}}. Sound good?"

When they confirm, call confirm_booking."""
            }
        ],
        "functions": [confirm_booking_schema],
    }


def create_appointment_confirmed_node() -> NodeConfig:
    """Create node for appointment confirmation"""
    return {
        "name": "appointment_confirmed",
        "task_messages": [
            {
                "role": "system",
                "content": """Briefly confirm and wrap up.

Example: "Perfect! Your confirmation number is {{confirmation_number}}. You'll get an email, and we'll call 30 minutes before. Anything else?"

Keep it short and friendly."""
            }
        ],
        "functions": [end_conversation_schema],
    }


def create_no_availability_node(alternative_times: list[str]) -> NodeConfig:
    """Create node for handling no availability"""
    times_list = ", ".join(alternative_times)
    return {
        "name": "no_availability",
        "task_messages": [
            {
                "role": "system",
                "content": f"""Apologize that the requested time isn't available and suggest alternatives.

Say something like: "That time's booked, but I have these slots open: {times_list}. Would any of those work?"

Be positive and helpful. Once they choose an alternative, use check_availability again."""
            }
        ],
        "functions": [availability_schema, end_conversation_schema],
    }


def create_reschedule_lookup_node() -> NodeConfig:
    """Create node for looking up appointment to reschedule"""
    return {
        "name": "reschedule_lookup",
        "task_messages": [
            {
                "role": "system",
                "content": """Look up the customer's existing appointment using the information they provided.

Say something like: "No problem. Let me pull up your appointment..."

Use the lookup_reschedule function to find their appointment and check the 24-hour policy."""
            }
        ],
        "functions": [reschedule_lookup_schema],
    }


def create_reschedule_new_time_node() -> NodeConfig:
    """Create node for rescheduling to new time"""
    now = datetime.now()
    current_date = now.strftime("%A, %B %d, %Y")
    tomorrow = (now + timedelta(days=1)).strftime("%A, %B %d, %Y")

    return {
        "name": "reschedule_new_time",
        "task_messages": [
            {
                "role": "system",
                "content": f"""Help the customer choose a new date and time.

IMPORTANT DATE CONTEXT:
- Today is: {current_date}
- Tomorrow is: {tomorrow}
- Business hours: 9 AM to 6 PM, Monday through Saturday

If the appointment was within 24 hours, mention: "Just so you know, since it's within 24 hours, there might be a rescheduling fee. Still want to reschedule?"

Then ask: "Okay, when works better for you?"

Once they provide it, use reschedule_to_new_time to update the appointment."""
            }
        ],
        "functions": [reschedule_execute_schema],
    }


def create_cancel_lookup_node() -> NodeConfig:
    """Create node for looking up appointment to cancel"""
    return {
        "name": "cancel_lookup",
        "task_messages": [
            {
                "role": "system",
                "content": """Look up the customer's appointment for cancellation.

Be understanding: "I understand. Let me look that up for you..."

Use the lookup_cancel function to find their appointment."""
            }
        ],
        "functions": [cancel_lookup_schema],
    }


def create_cancel_decision_node(within_24h: bool, appointment_time: str) -> NodeConfig:
    """Create node for cancel decision"""
    if within_24h:
        message = f"""The appointment is within 24 hours. Explain the cancellation fee.

Say: "Your appointment's coming up soon at {{appointment_time}}, so there's a $75 cancellation fee. Do you still want to cancel, or would you prefer to reschedule?"

Listen for their decision."""
    else:
        message = f"""The appointment is not within 24 hours.

Say: "I found your appointment for {{appointment_time}}. I can cancel that for you. Should I go ahead, or would you rather reschedule?"

Listen for their decision."""

    return {
        "name": "cancel_decision",
        "task_messages": [
            {
                "role": "system",
                "content": message
            }
        ],
        "functions": [proceed_cancel_schema, reschedule_request_schema, keep_appointment_schema],
    }


def create_cancellation_confirmed_node() -> NodeConfig:
    """Create node for cancellation confirmation"""
    return {
        "name": "cancellation_confirmed",
        "task_messages": [
            {
                "role": "system",
                "content": """Confirm the cancellation warmly.

Say: "Done. Your appointment is cancelled. If you need anything in the future, just give us a call. Anything else I can help with?"

Be understanding and positive."""
            }
        ],
        "functions": [end_conversation_schema],
    }


def create_product_info_node() -> NodeConfig:
    """Create node for product information"""
    return {
        "name": "product_info",
        "task_messages": [
            {
                "role": "system",
                "content": """Provide information about World of Doors services confidently and naturally.

Say something like: "Sure! We handle garage door repair, installation, maintenance, and inspections. We're known for quality work and reliable service with competitive pricing. We usually have same-day or next-day appointments available. Would you like to schedule a service?"

Be helpful and conversational."""
            }
        ],
        "functions": [product_inquiry_schema],
    }


def create_end_node() -> NodeConfig:
    """Create the final node"""
    return {
        "name": "end",
        "task_messages": [
            {
                "role": "system",
                "content": """Thank them warmly and end the conversation.

Say something like: "Perfect! Alright, we'll see you then... have a great day!"

Be warm and genuine."""
            }
        ],
        "post_actions": [{"type": "end_conversation"}],
    }
