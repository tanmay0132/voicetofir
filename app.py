import os
import json
import io
import streamlit as st
from dotenv import load_dotenv
from audio_recorder_streamlit import audio_recorder
from streamlit_drawable_canvas import st_canvas
from groq import Groq
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Optional
from gtts import gTTS
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# --- LOAD ENVIRONMENT VARIABLES ---
load_dotenv()

groq_api_key = os.getenv("GROQ_API_KEY")
gemini_api_key = os.getenv("GEMINI_API_KEY")

# --- PAGE SETUP ---
st.set_page_config(page_title="Voice-to-FIR System", page_icon="⚖️", layout="wide")
st.title("⚖️ Voice-to-FIR & Police Complaint Portal")

# Session state initialization for interactive multi-turn flow
if "fir_data" not in st.session_state:
    st.session_state.fir_data = None
if "conversation_history" not in st.session_state:
    st.session_state.conversation_history = ""

# --- SIDEBAR CONFIG ---
with st.sidebar:
    st.header("⚙️ Portal Settings")
    mode = st.radio("Select View", ["Citizen Complaint Portal", "SHO Review Dashboard"])
    st.markdown("---")

    # Check .env configuration status
    if groq_api_key and gemini_api_key:
        st.success("Welcome to our server")
    else:
        st.error("❌ Missing keys in .env! Ensure GROQ_API_KEY and GEMINI_API_KEY are defined.")


# --- SCHEMA ---
class IncidentLocation(BaseModel):
    area_or_street: str = Field(..., description="Colony, street, or landmark")
    city_district: str = Field(..., description="City or district")


class FIRReport(BaseModel):
    incident_category: str
    time_of_incident: str
    location: IncidentLocation
    complainant_name: Optional[str] = "Citizen"
    accused_details: Optional[str] = "Unknown"
    stolen_items: List[str] = Field(default_factory=list)
    formal_narrative: str
    suggested_bns_sections: List[str]
    missing_critical_info: List[str]
    clarification_question_hindi: Optional[str] = Field(None,
                                                        description="One natural question in Hindi asking for the missing info")


# Helper: Generate PDF
def generate_pdf(fir_dict):
    buffer = io.BytesIO()
    p = canvas.Canvas(buffer, pagesize=letter)
    p.setFont("Helvetica-Bold", 16)
    p.drawString(160, 750, "FIRST INFORMATION REPORT (DRAFT)")
    p.setFont("Helvetica", 11)

    p.drawString(50, 710, f"Offense Category: {fir_dict.get('incident_category')}")
    p.drawString(50, 690, f"Time of Incident: {fir_dict.get('time_of_incident')}")
    loc = fir_dict.get('location', {})
    p.drawString(50, 670, f"Location: {loc.get('area_or_street')}, {loc.get('city_district')}")
    p.drawString(50, 650, f"Complainant: {fir_dict.get('complainant_name')}")
    p.drawString(50, 630, f"Suggested BNS Sections: {', '.join(fir_dict.get('suggested_bns_sections', []))}")

    p.setFont("Helvetica-Bold", 12)
    p.drawString(50, 590, "Statement Narrative:")
    p.setFont("Helvetica", 10)

    text = p.beginText(50, 570)
    text.setFont("Helvetica", 10)
    for line in fir_dict.get('formal_narrative', '').split('. '):
        text.textLine(line + '.')
    p.drawText(text)

    p.drawString(50, 200, "Complainant Signature: _______________________")
    p.showPage()
    p.save()
    buffer.seek(0)
    return buffer


# ==================== VIEW 1: CITIZEN PORTAL ====================
if mode == "Citizen Complaint Portal":
    st.subheader("1. Speak Your Complaint (Hindi / English)")
    audio_bytes = audio_recorder(text="Click to Speak", recording_color="#e74c3c", neutral_color="#2ecc71")

    if audio_bytes:
        if not groq_api_key or not gemini_api_key:
            st.error("Missing API Keys! Please configure GROQ_API_KEY and GEMINI_API_KEY in your .env file.")
        else:
            if st.button("Submit Voice Statement"):
                with st.spinner("Transcribing speech..."):
                    # Transcribe with Groq Whisper
                    groq_client = Groq(api_key=groq_api_key)
                    transcription = groq_client.audio.transcriptions.create(
                        file=("audio.wav", audio_bytes),
                        model="whisper-large-v3"
                    )
                    new_input = transcription.text
                    st.session_state.conversation_history += f"\nCitizen: {new_input}"

                with st.spinner("Structuring FIR with Gemini..."):
                    # Analyze with Gemini
                    gemini_client = genai.Client(api_key=gemini_api_key)
                    prompt = f"""
                    You are a legal assistant in India registering an FIR under BNS.
                    Cumulative citizen statements:
                    {st.session_state.conversation_history}

                    Extract all data into schema. If critical info is missing, write a polite clarification question in Hindi.
                    """
                    response = gemini_client.models.generate_content(
                        model="gemini-3.5-flash-lite",
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=FIRReport,
                            temperature=0.1
                        )
                    )
                    st.session_state.fir_data = json.loads(response.text)

    # Display Clarification / Voice Readback Loop
    if st.session_state.fir_data:
        data = st.session_state.fir_data

        if data.get("missing_critical_info"):
            st.warning("⚠️ **Missing Information Required**")
            for gap in data["missing_critical_info"]:
                st.write(f"- {gap}")

            # Interactive Voice Clarification via free gTTS
            if data.get("clarification_question_hindi"):
                q_text = data["clarification_question_hindi"]
                st.info(f"🤖 **Assistant Question:** {q_text}")

                tts = gTTS(text=q_text, lang='hi')
                tts.save("question.mp3")
                st.audio("question.mp3", format="audio/mp3")
                st.caption("Record another voice note above answering this question to update the draft.")

        st.markdown("---")
        st.subheader("2. Formal Draft & Digital Signature")
        st.write(f"**Offense:** {data.get('incident_category')}")
        st.write(f"**BNS Penal Sections:** {', '.join(data.get('suggested_bns_sections', []))}")
        st.info(data.get("formal_narrative"))

        st.write("**Citizen Signature:**")
        canvas_result = st_canvas(stroke_width=2, stroke_color="#000000", background_color="#f0f2f6", height=120,
                                  width=400, drawing_mode="freedraw", key="canvas")

        pdf_file = generate_pdf(data)
        st.download_button(
            label="📄 Download Official FIR Draft (PDF)",
            data=pdf_file,
            file_name="FIR_Draft.pdf",
            mime="application/pdf"
        )

# ==================== VIEW 2: SHO REVIEW DASHBOARD ====================
elif mode == "SHO Review Dashboard":
    st.subheader("Station House Officer (SHO) Review Console")
    if not st.session_state.fir_data:
        st.info("No complaint lodged yet in this session.")
    else:
        d = st.session_state.fir_data
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"**Complainant:** {d.get('complainant_name')}")
            st.write(f"**Occurred At:** {d.get('time_of_incident')}")
            st.write(f"**Location:** {d.get('location', {}).get('area_or_street')}")
        with col2:
            st.write(f"**Suggested Sections:** {', '.join(d.get('suggested_bns_sections', []))}")
            status = st.selectbox("Action",
                                  ["Pending Review", "Approve & Register e-FIR", "Reject / Need More Evidence"])

        st.text_area("Legal Statement", d.get("formal_narrative"), height=150)
        if st.button("Finalize FIR Entry"):
            st.success("FIR has been officially logged into the station diary!")