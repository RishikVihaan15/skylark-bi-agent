"""
Conversational agent using Google Gemini with tool-use.
Uses the Chat API which automatically handles thought_signatures for thinking models.
"""
import os
from google import genai
from google.genai import types

from .tools import TOOL_DEFINITIONS, dispatch_tool

MODEL = "gemini-3.5-flash-lite"

SYSTEM_PROMPT = """You are a BI assistant for Skylark Drones. Answer questions by querying two live monday.com boards: work_orders (execution) and deals (pipeline). Never use hardcoded data.

work_orders columns: item_name, Customer Name Code, Execution Status, Date of PO/LOI, Probable Start Date, Probable End Date, Data Delivery Date, Sector, Type of Work, BD/KAM Personnel code, Amount in Rupees (Excl of GST) (Masked), Billed Value in Rupees (Excl of GST.) (Masked), Collected Amount in Rupees (Incl of GST.) (Masked), Amount Receivable (Masked)
Execution Status: Completed, Ongoing, Not Started, Partial Completed, Pause/struck, Details pending, Executed until current month
Sector: Mining, Powerline, Railways, Renewables, Construction, Others

deals columns: item_name, Owner code, Deal Status, Deal Stage, Masked Deal value, Sector/service, Tentative Close Date, Created Date
Deal Status: Open, Won, Dead, On Hold (filter out header row 'Deal Status')
Deal Stage order: A.Lead, B.SQL, C.Demo, D.Feasibility, E.Proposal, F.Negotiations, G.Won, H.WO Received, I.POC, J.Invoice, K.Accrued, L.Lost, M.Hold, Project Completed
Sector: Mining, Powerline, Railways, Renewables, Construction, Aviation, Manufacturing, DSP, Security, Tender, Others
CAVEAT: Masked Deal value is 52.3% missing — always flag. Use Tentative Close Date for dates.

STEPS: 1) Call get_schema_overview first. 2) Call get_distinct_values before filtering text columns. 3) Use run_analysis — assign answer to `result`, no imports. 4) Answer concisely: number first, brief context, key caveats only."""


def _build_tools() -> list:
    """Convert TOOL_DEFINITIONS to Gemini function declarations."""
    declarations = []
    for t in TOOL_DEFINITIONS:
        schema = t["input_schema"]
        props = {}
        for name, prop in schema.get("properties", {}).items():
            p = {"type": prop.get("type", "string").upper()}
            if "description" in prop:
                p["description"] = prop["description"]
            if "enum" in prop:
                p["enum"] = prop["enum"]
            props[name] = types.Schema(**p)

        declarations.append(
            types.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=types.Schema(
                    type="OBJECT",
                    properties=props,
                    required=schema.get("required", []),
                ),
            )
        )
    return [types.Tool(function_declarations=declarations)]


def get_client():
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")
    return genai.Client(api_key=api_key)


def new_chat(client):
    """Create a new Gemini chat session."""
    tools = _build_tools()
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=tools,
        temperature=0,
    )
    return client.chats.create(model=MODEL, config=config)


def run_agent_turn(chat_session, user_message: str) -> str:
    """
    Sends user message to Gemini chat, handles tool calls in a loop,
    returns the final text response.
    """
    try:
        response = chat_session.send_message(user_message)
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}") from e

    # Tool-use loop
    while True:
        # Collect any function calls from the response
        fn_calls = []
        for part in response.candidates[0].content.parts:
            if part.function_call:
                fn_calls.append(part.function_call)

        if not fn_calls:
            # No more tool calls — extract and return text
            text_parts = []
            for part in response.candidates[0].content.parts:
                if part.text:
                    text_parts.append(part.text)
            return "\n".join(text_parts) or "(no response)"

        # Execute all tool calls and send results back
        tool_results = []
        for fn_call in fn_calls:
            name = fn_call.name
            args = dict(fn_call.args) if fn_call.args else {}
            result = dispatch_tool(name, args)
            tool_results.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        name=name,
                        response={"result": result},
                    )
                )
            )

        try:
            response = chat_session.send_message(tool_results)
        except Exception as e:
            raise RuntimeError(f"Gemini API error: {e}") from e
