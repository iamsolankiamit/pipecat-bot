"""
Appointment booking conversation flow using Pipecat-Flows
"""

from loguru import logger
from src.handlers.appointment_handlers import (
    handle_new_appointment,
    handle_reschedule,
    handle_cancel,
    set_service_type,
    check_calendar,
    save_contact_info,
    confirm_and_book,
    handle_modification,
    end_call,
    check_reschedule_policy,
    reschedule_appointment,
    cancel_appointment_handler,
    keep_appointment
)


def create_appointment_flow(bot):
    """
    Create the appointment booking conversation flow

    This uses a simplified structure that will work with Pipecat's pipeline.
    The actual Pipecat-Flows implementation would use FlowConfig, but for now
    we'll create a state-based flow that can be integrated with the LLM service.

    Args:
        bot: WorldOfDoorsBot instance

    Returns:
        Flow configuration dict
    """

    # System prompts for different conversation stages
    flow_config = {
        "initial_node": "greeting",
        "nodes": {
            # Node 1: Greeting and intent detection
            "greeting": {
                "system_prompt": """You are Jordan, a friendly scheduling assistant for World of Doors garage door service company.

Greet the customer warmly and ask what you can help them with today. Listen for:
- New appointment scheduling
- Rescheduling an existing appointment
- Cancelling an appointment

Keep your greeting brief and natural. Example: "Hi! This is Jordan from World of Doors. How can I help you today?"
""",
                "functions": [
                    {
                        "name": "new_appointment",
                        "description": "Customer wants to schedule a new appointment",
                        "handler": handle_new_appointment,
                        "parameters": {}
                    },
                    {
                        "name": "reschedule_appointment",
                        "description": "Customer wants to reschedule existing appointment",
                        "handler": handle_reschedule,
                        "parameters": {
                            "confirmation_number": {
                                "type": "string",
                                "description": "The appointment confirmation number"
                            }
                        }
                    },
                    {
                        "name": "cancel_appointment",
                        "description": "Customer wants to cancel appointment",
                        "handler": handle_cancel,
                        "parameters": {
                            "confirmation_number": {
                                "type": "string",
                                "description": "The appointment confirmation number"
                            }
                        }
                    }
                ],
                "transitions": {
                    "new_appointment": "service_type",
                    "reschedule_appointment": "reschedule_policy_check",
                    "cancel_appointment": "cancel_policy_check"
                }
            },

            # Node 2: Collect service type
            "service_type": {
                "system_prompt": """Ask the customer what type of service they need. The options are:
- Repair (for broken or malfunctioning garage doors)
- Installation (for new garage door installation)
- Maintenance (for routine maintenance and tune-ups)
- Emergency (for urgent same-day service)

Keep it conversational. Example: "What type of service do you need today - is it a repair, new installation, maintenance, or an emergency?"
""",
                "functions": [
                    {
                        "name": "set_service_type",
                        "description": "Record the type of service the customer needs",
                        "handler": set_service_type,
                        "parameters": {
                            "service_type": {
                                "type": "string",
                                "enum": ["REPAIR", "INSTALLATION", "MAINTENANCE", "EMERGENCY"],
                                "description": "The type of service needed"
                            },
                            "issue_description": {
                                "type": "string",
                                "description": "Brief description of the issue (optional)"
                            }
                        },
                        "required": ["service_type"]
                    }
                ],
                "transitions": {
                    "set_service_type": "check_availability"
                }
            },

            # Node 3: Check calendar availability
            "check_availability": {
                "system_prompt": """Ask the customer for their preferred date and time for the appointment.

After they tell you, let them know you're checking the schedule, then call the check_calendar function.

Example: "What date and time works best for you?" ... "Let me check our schedule for [date]..."
""",
                "pre_actions": [
                    {"type": "say", "text": "Let me check our availability..."}
                ],
                "functions": [
                    {
                        "name": "check_calendar",
                        "description": "Check available appointment slots for a given date",
                        "handler": check_calendar,
                        "parameters": {
                            "preferred_date": {
                                "type": "string",
                                "description": "Date in YYYY-MM-DD format"
                            },
                            "preferred_time": {
                                "type": "string",
                                "description": "Preferred time (optional)"
                            }
                        },
                        "required": ["preferred_date"]
                    }
                ],
                "transitions": {
                    "check_calendar": "collect_contact_info"
                }
            },

            # Node 4: Collect contact information
            "collect_contact_info": {
                "system_prompt": """Now collect the customer's contact information for the appointment.

You need:
- Full name
- Phone number (if not already on file)
- Email address (optional but recommended)

Be conversational and explain why: "Great! I just need a few details to complete your booking. Can I get your full name?"
""",
                "functions": [
                    {
                        "name": "save_contact_info",
                        "description": "Save customer contact information",
                        "handler": save_contact_info,
                        "parameters": {
                            "name": {
                                "type": "string",
                                "description": "Customer's full name"
                            },
                            "phone": {
                                "type": "string",
                                "description": "Customer's phone number"
                            },
                            "email": {
                                "type": "string",
                                "description": "Customer's email address"
                            }
                        },
                        "required": ["name"]
                    }
                ],
                "transitions": {
                    "save_contact_info": "confirm_booking"
                }
            },

            # Node 5: Confirm and book
            "confirm_booking": {
                "system_prompt": """Confirm all the appointment details with the customer:
- Service type
- Date and time
- Their name and contact info

Ask if everything looks correct before booking.

Example: "Let me confirm: I have you scheduled for [service type] on [date] at [time]. Your name is [name] and we'll send confirmation to [email]. Does everything look correct?"
""",
                "functions": [
                    {
                        "name": "confirm_and_book",
                        "description": "Customer confirms - create the appointment",
                        "handler": confirm_and_book,
                        "parameters": {}
                    },
                    {
                        "name": "modify_details",
                        "description": "Customer wants to change something",
                        "handler": handle_modification,
                        "parameters": {
                            "what_to_change": {
                                "type": "string",
                                "description": "What the customer wants to change"
                            }
                        }
                    }
                ],
                "transitions": {
                    "confirm_and_book": "appointment_confirmed",
                    "modify_details": "check_availability"  # Go back to modify
                }
            },

            # Node 6: Appointment confirmed
            "appointment_confirmed": {
                "system_prompt": """Thank the customer and provide their confirmation number. Let them know they'll receive a confirmation email.

Be warm and professional. Example: "Perfect! Your appointment is confirmed. Your confirmation number is [number]. You'll receive a confirmation email shortly. Is there anything else I can help you with today?"
""",
                "functions": [
                    {
                        "name": "end_call",
                        "description": "End the conversation",
                        "handler": end_call,
                        "parameters": {}
                    }
                ],
                "transitions": {
                    "end_call": None  # End of flow
                }
            },

            # Reschedule flow nodes
            "reschedule_policy_check": {
                "system_prompt": """You're helping the customer reschedule their appointment.

First, look up their appointment using the confirmation number. Check if it's within 24 hours.

If within 24 hours, explain: "I see your appointment is within 24 hours. There may be a rescheduling fee, but I can still help you. Would you like to proceed?"

If not within 24 hours, proceed normally: "No problem, let me help you reschedule. What date and time would work better for you?"
""",
                "functions": [
                    {
                        "name": "check_reschedule_policy",
                        "description": "Check if appointment can be rescheduled and any fees",
                        "handler": check_reschedule_policy,
                        "parameters": {
                            "confirmation_number": {
                                "type": "string",
                                "description": "Appointment confirmation number"
                            }
                        },
                        "required": ["confirmation_number"]
                    }
                ],
                "transitions": {
                    "check_reschedule_policy": "execute_reschedule"
                }
            },

            "execute_reschedule": {
                "system_prompt": """Help the customer choose a new date and time for their appointment.

Ask: "What date and time would work better for you?"

After they respond, check availability and reschedule the appointment.
""",
                "functions": [
                    {
                        "name": "reschedule_to_new_time",
                        "description": "Reschedule appointment to new date/time",
                        "handler": reschedule_appointment,
                        "parameters": {
                            "new_date": {
                                "type": "string",
                                "description": "New date in YYYY-MM-DD format"
                            },
                            "new_time": {
                                "type": "string",
                                "description": "New time"
                            }
                        },
                        "required": ["new_date"]
                    }
                ],
                "transitions": {
                    "reschedule_to_new_time": "appointment_confirmed"
                }
            },

            # Cancel flow nodes
            "cancel_policy_check": {
                "system_prompt": """The customer wants to cancel their appointment.

Look up the appointment and check if it's within 24 hours.

If within 24 hours, explain: "I can help you cancel, but since it's within 24 hours of your appointment, there will be a $75 cancellation fee. Would you still like to cancel?"

If not within 24 hours: "I can help you cancel your appointment. May I ask if there's anything we could do to keep you scheduled, or would you prefer to cancel?"
""",
                "functions": [
                    {
                        "name": "proceed_with_cancel",
                        "description": "Customer confirms they want to cancel",
                        "handler": cancel_appointment_handler,
                        "parameters": {
                            "confirmation_number": {
                                "type": "string",
                                "description": "Appointment confirmation number"
                            }
                        },
                        "required": ["confirmation_number"]
                    },
                    {
                        "name": "keep_appointment",
                        "description": "Customer decides to keep the appointment",
                        "handler": keep_appointment,
                        "parameters": {}
                    }
                ],
                "transitions": {
                    "proceed_with_cancel": "appointment_confirmed",
                    "keep_appointment": "appointment_confirmed"
                }
            }
        }
    }

    logger.info("Appointment flow created")
    return flow_config
