import imaplib
import email
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
from typing import List
from email.message import EmailMessage
from datetime import date
import requests
import pypdf
import io
import smtplib
from langchain.tools import tool
from dotenv import load_dotenv
import os

load_dotenv()
# Setup email credentials and configurations
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "eofeh-mamuzoh.2301630@stu.cu.edu.ng"
SENDER_PASSWORD = "psos pbrz rnmt snwn"  # Use your 16-character App Password here!
RECEIVER_EMAIL = "eofeh-mamuzoh.2301630@stu.cu.edu.ng"

# Model configuration with separate independent fallback API keys
MODEL_PRIORITY_LIST = [
    {
        "name": "gemini-2.5-flash",
        "api_key": os.getenv("GEMINI_PRIMARY_KEY"),
        "provider": "google"
    },
    {
        "name": "gemini-3.1-flash-lite",
        "api_key": os.getenv("GEMINI_BACKUP_KEY"),
        "provider": "google"
    }
]


def send_error_alert(error_message: str, process_name: str):
    """Sends an automated email alert when any component or process in the system fails."""
    msg = EmailMessage()
    msg["Subject"] = f"ALERT: Email Assistant Failure in {process_name}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL

    body = f"An execution error was intercepted dynamically:\n\nProcess: {process_name}\n\nTechnical Error Details:\n{error_message}"
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        print("Diagnostic alert email successfully dispatched to administrator.")
    except Exception as smtp_err:
        print(f"Failed to deliver error alert email. Severe Network/STMP Error: {smtp_err}")


def safe_llm_call(messages: list, response_schema=None):
    """Iterates through the model priority list to execute API requests dynamically when a quota is finished or a model is acting up."""
    for config in MODEL_PRIORITY_LIST:
        try:
            if config["provider"] == "google":
                model = ChatGoogleGenerativeAI(
                    model=config["name"],
                    temperature=0,
                    google_api_key=config["api_key"]
                )

                if response_schema:
                    structured_llm = model.with_structured_output(response_schema)
                    return structured_llm.invoke(messages)
                else:
                    response = model.invoke(messages)
                    return response.content
        except Exception as e:
            print(f"Model {config['name']} failed or quota finished. Error: {e}. Switching to next fallback model...")
            continue

    raise RuntimeError("All configured fallback models in the priority list failed to execute.")


# connecting to my mail by instantiating a connection
mail = imaplib.IMAP4_SSL("imap.gmail.com")
mail.login(SENDER_EMAIL, SENDER_PASSWORD)
mail.select("inbox")

# collect only email for today
date_today = date.today()
today_str = date_today.strftime("%d-%b-%Y")

search_criterion = f"SINCE {today_str} TEXT 'shareholders' TEXT 'dividend'"

status, data = mail.search(None, search_criterion)

emails_to_analyse = []

if data[0] == b'':
    print("No email today")
else:
    email_ids = data[0].split()
    # to get the emails to analyse using the FETCH method we will initialise an empty list
    for e_id in email_ids:
        status, msg_data = mail.fetch(e_id, "(RFC822)")
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)
        emails_to_analyse.append(msg)


# Define the structure you want back
class ExtractedLinks(BaseModel):
    pdf_urls: List[str] = Field(
        description="List of URLs that point to download documents like proxy forms, notices, or annual reports.")


def extract_links_with_gemini(html_content: str) -> List[str]:
    # 1. Clean the HTML slightly so we don't send massive files to the LLM
    soup = BeautifulSoup(html_content, "html.parser")

    # Extract all links with their text so the LLM has context
    extracted_anchors = []
    for a in soup.find_all('a', href=True):
        extracted_anchors.append(f"Text: {a.text.strip()} | URL: {a['href']}")

    context_str = "\n".join(extracted_anchors)
    if not context_str:
        return []

    # 2. Setup your Gemini Model (Note: Use "gemini-1.5-flash" or "gemini-2.5-flash")
    system_prompt = "You are an assistant that identifies document download URLs (Notices, Proxy forms, Annual Reports) from a list of extracted email links."
    human_prompt = f"Analyze these links extracted from a shareholder email and return ONLY the URLs that allow downloading the PDF documents:\n\n{context_str}"

    try:
        messages = [
            ("system", system_prompt),
            ("human", human_prompt)
        ]
        response = safe_llm_call(messages, response_schema=ExtractedLinks)
        return response.pdf_urls
    except Exception as e:
        send_error_alert(str(e), "Agent 1: Link Extraction Tool Loop")
        return []


def download_and_read_pdf(url: str) -> str:
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            # Load PDF bytes directly into memory
            pdf_file = io.BytesIO(response.content)
            reader = pypdf.PdfReader(pdf_file)

            # Extract text from all pages
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        else:
            send_error_alert(f"Status code received: {response.status_code} for URL: {url}", "Tool: PDF Downloader")
            return f"Failed to download PDF. Status code: {response.status_code}"
    except Exception as e:
        send_error_alert(str(e), f"Tool: PDF Downloader Critical Exception on {url}")
        return f"Error downloading PDF: {str(e)}"


# creating briefing
def create_brief(text: str):
    """this function is to create a briefing from the text"""
    system_Prompt = "You are a smart financial assistant in terms of stocks. Write in clean paragraphs with simple line breaks. Do not use Markdown asterisks or bold text headers."

    try:
        messages = [
            ("system", system_Prompt),
            ("human", f"create a financial summary of this {text}")
        ]
        return safe_llm_call(messages)
    except Exception as e:
        send_error_alert(str(e), "Agent 1: Briefing Generation Task")
        return "Error creating briefing summary due to total model infrastructure failures."


# --- Main Execution Workflow Orchestration ---

if emails_to_analyse:
    for m in emails_to_analyse:
        html_body = ""
        plain_body = ""

        # Corrected structure to cleanly grab the content types
        if m.is_multipart():
            for part in m.walk():
                content_type = part.get_content_type()
                if content_type == "text/html":
                    html_body = part.get_payload(decode=True).decode(errors="ignore")
                elif content_type == "text/plain":
                    plain_body = part.get_payload(decode=True).decode(errors="ignore")
        else:
            if m.get_content_type() == "text/html":
                html_body = m.get_payload(decode=True).decode(errors="ignore")
            else:
                plain_body = m.get_payload(decode=True).decode(errors="ignore")

        # Determine the target content to look for URLs in
        target_content = html_body if html_body else plain_body

        if not target_content:
            print("Skipping email: No readable body found.")
            continue

        # Extract the relevant document links using Gemini
        links = extract_links_with_gemini(target_content)

        if not links:
            print("No relevant shareholder PDF document links found in this email.")
            continue

        # Accumulate text from all extracted PDF links
        combined_pdf_text = ""
        for url in links:
            print(f"Downloading and reading: {url}")
            pdf_text = download_and_read_pdf(url)
            combined_pdf_text += f"\n--- Document from {url} ---\n" + pdf_text

        # Generate the final briefing from the accumulated text
        print("Generating financial briefing summary...")
        final_briefing = create_brief(combined_pdf_text)

        # sending the brief via email
        msg = EmailMessage()
        msg["Subject"] = "Stock briefing from a meeting from your personal email assistant"
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECEIVER_EMAIL
        msg.set_content(final_briefing)  # Assign the briefing text to the email body

        try:
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
                server.starttls()  # Upgrade the connection to secure encrypted TLS
                server.login(SENDER_EMAIL, SENDER_PASSWORD)
                server.send_message(msg)
            print("Email sent successfully!")
        except Exception as e:
            send_error_alert(str(e), "Agent 1: Dispatch Final Summary Email")
            print(f"Failed to send email. Error: {e}")

# Close the IMAP mailbox connection cleanly
mail.close()
mail.logout()


# -------------- agent 2 for the stock meeting

@tool
def TavilyStockSearch():
    """Searches the web for default stock market news for Access Bank, GTCO, and Zenith Bank."""
    from tavily import TavilyClient
    tavily_client = TavilyClient(api_key=os.getenv("YOUR_TAVILY_API_KEY"))
    response = tavily_client.search(
        "Stock market news, corporate actions, and quarterly financial performance for Access Bank, GTCO, and Zenith Bank")
    return response


# send stock news to my email
@tool
def Stock_news_mail(new: str) -> str:
    """Sends compiled financial and stock market news updates via email."""
    smtp_server = "smtp.gmail.com"
    smtp_port = 587
    sender_email = "eofeh-mamuzoh.2301630@stu.cu.edu.ng"
    sender_password = "Elozino123."
    reciever_email = "eofeh-mamuzoh.2301630@stu.cu.edu.ng"
    msg = EmailMessage()
    msg["Subject"] = "stock news"
    msg["From"] = sender_email
    msg["To"] = reciever_email
    msg.set_content(new)

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()  # Upgrade the connection to secure encrypted TLS
            server.login(sender_email, sender_password)
            server.send_message(msg)
        print("Email sent successfully!")
    except Exception as e:
        send_error_alert(str(e), "Agent 2: Tool Stock_news_mail Email Delivery")
        print(f"Failed to send email. Error: {e}")


# Optimize your search tool to dynamically accept queries and use parameters
@tool
def tavily_stock_search(query: str) -> dict:
    """Searches the web for latest financial news using Tavily."""
    from tavily import TavilyClient
    try:
        # Remember to insert your actual Tavily API Key
        tavily_client = TavilyClient(api_key="YOUR_TAVILY_API_KEY")

        # We pass the time_range="week" parameter to restrict it to the last 7 days
        response = tavily_client.search(
            query=query,
            time_range="week",
            topic="news",
            max_results=5
        )
        return response
    except Exception as e:
        send_error_alert(str(e), "Agent 2: Tool tavily_stock_search Request Execution")
        return {}


# Main function to run the Agent 2 pipeline
def run_weekly_stock_news_agent():
    print("Running Agent 2: Fetching weekly stock market news...")

    query_string = "Stock market news, corporate actions, and quarterly financial performance for Access Bank, GTCO, and Zenith Bank"

    # 1. Execute Tavily Search
    raw_search_results = tavily_stock_search(query_string)

    # 2. Extract contents from search results to feed to Gemini
    articles_context = ""
    if "results" in raw_search_results:
        for result in raw_search_results["results"]:
            articles_context += f"\nTitle: {result.get('title')}\nContent: {result.get('content')}\nURL: {result.get('url')}\n---"

    if not articles_context:
        print("No stock news found for your portfolio this week.")
        return

    # 3. Use Gemini to structure a neat news summary report
    system_prompt = "You are an expert financial analyst. Compile a comprehensive weekly stock news digest from the provided text context. Do not use Markdown asterisks or bold headers."
    human_prompt = f"Summarize the major news, price movements, and corporate actions for Access Bank, GTCO, and Zenith Bank from this weekly context:\n\n{articles_context}"

    try:
        messages = [
            ("system", system_prompt),
            ("human", human_prompt)
        ]
        weekly_digest = safe_llm_call(messages)

        # 4. Use your existing tool to mail it out
        Stock_news_mail(weekly_digest)

    except Exception as e:
        send_error_alert(str(e), "Agent 2: Pipeline Core Compilation Logic")
        print(f"Agent 2 failed to compile summary: {e}")


# --- Weekday Conditional Trigger ---
# 5 is Saturday, 6 is Sunday
if date.today().weekday() >= 5:
    run_weekly_stock_news_agent()
else:
    print("Skipping Agent 2: Stock news updates run exclusively on weekends.")