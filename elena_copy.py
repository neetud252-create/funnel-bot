"""Elena's local, translated setup help. No external AI request is made here."""
import localization
import journey

INTRO = 'Hi, I’m Elena 👋 Your Pocket IQ personal assistant.'
ACTIVATE_BUTTON = '💬 Message Elena to Activate'
OPEN_BOT = '🚀 Open Pocket IQ'
ACTIVATION = (
    '👋 <b>Welcome to Pocket IQ</b>\n\n'
    '<b>Activate your bot access</b>\n'
    'Message Elena, your Pocket IQ personal assistant, to get started.\n\n'
    'Tap the button below and press <b>Send</b> in Elena’s chat using this same Telegram account.\n\n'
    'Pocket IQ will automatically show your menu here after you send the message.\n\n'
    'Replies in Elena’s chat are automated. Account verification and signal access requirements still apply.')
ACTIVATED = (
    '✅ <b>Your Pocket IQ account has been activated successfully!</b>\n\n'
    'Your menu is opening in @goplusaitrader_bot. Tap the username to return.\n\n'
    'I’m Elena, your Pocket IQ personal assistant. I can help you with setup.')
QUESTION = 'What would you like help with? Choose an option below.'
INVITE = 'Need help finishing your Pocket IQ setup? Chat with Elena or continue here.'
PAUSED = 'Follow-ups are paused. You can return whenever you are ready.'
STOPPED = 'Reminders are stopped. Your bot access is unchanged.'
HUMAN = 'Automated replies are paused so a member of the team can help you in this chat.'
OFFLINE = 'Elena’s automatic chat is not connected yet. You can continue setup in Pocket IQ.'
UID = 'Open your Pocket Option profile to find your Trader ID, then submit it inside Pocket IQ using the button below.'
FALLBACK = 'Choose a topic below, or request a person for help with your message.'
CONSENT = 'I’m Elena, Pocket IQ’s AI assistant. With your permission, OpenAI receives your message, a short redacted conversation, language, setup status, verification result and qualifying-deposit amount to answer you. Please do not send passwords, login codes, payment details or Trader IDs. Redacted chat history is kept here for up to 24 hours. You can use help buttons or request a person instead. Voice is optional: tapping Listen sends the reply text to OpenAI to create an AI voice. Stop revokes permission and clears this chat memory.'
AGREE = 'Allow AI replies'
BUTTONS_ONLY = 'Use help buttons only'
LISTEN = '🔊 Listen · AI voice via OpenAI'
AI_VOICE = 'AI-generated voice · Elena, Pocket IQ'
VOICE_UNAVAILABLE = 'Voice is unavailable right now. You can read the reply above.'
REOPEN = 'Send Elena a new message to continue this chat.'
RESUME = 'Resume replies'
REPLIES_PAUSED = REOPEN
HELLO = 'Hi 👋 How can I help you?'
THANKS = 'You’re welcome!'
CHAT_HELP = 'Tell me what is going wrong with your setup, and I’ll help with the next step.'
CHAT_REGISTER = 'Open the registration link and complete your details and account confirmation. Then copy your Trader ID from your Pocket Option profile and submit it in Pocket IQ under Setup My Account.'
CHAT_EXISTING = 'If you already have a Pocket Option account, sign in and copy your Trader ID from your profile. Submit it in Pocket IQ to check access. Please do not create a second account.'
CHAT_UID = 'Copy the numeric Trader ID from your Pocket Option profile, then send it in Pocket IQ under Setup My Account. If it was rejected, check for a typo and try again.'
CHAT_SIGNALS = 'In Pocket IQ, choose Get Signals, then select your market, category, asset and expiration. You’ll get a chart, BUY or SELL and a short reason when live data is available.'
CHAT_PAYMENT = 'Payments and withdrawals are handled inside your Pocket Option account. If a payment is missing, recheck your status in Pocket IQ or contact the broker’s support before depositing again.'
CHAT_ASK = 'May I use OpenAI for personalised replies with your message, recent redacted chat and setup, verification and qualifying-deposit details? Reply YES or NO. /privacy explains more.'
CHAT_PRIVACY = 'Elena’s replies are automated using OpenAI with redacted messages, recent chat, language, setup, verification and qualifying-deposit details. Local chat memory expires after 24 hours. Never send passwords, login codes or payment details. /voice requests an AI voice; /basic uses local help; /human requests the team; /stop stops replies and clears local memory.'
CHAT_BASIC = 'Sure, I can still help with basic setup here. Send your question. You can type /ai later for personalised replies.'
CHAT_PAUSED = REOPEN
CHAT_VOICE = 'Ask a question, then send /voice to hear the latest reply in an AI voice.'
CHAT_NOTICE = 'Elena’s replies are automated using OpenAI with redacted chat and setup details. /privacy'
CHAT_UNAVAILABLE = 'Personalised AI replies are unavailable right now. I can still help with basic setup.'
SOURCES = (INTRO, QUESTION, INVITE, PAUSED, STOPPED, HUMAN, OFFLINE, UID, FALLBACK,
           CONSENT, AGREE, BUTTONS_ONLY, LISTEN, AI_VOICE, VOICE_UNAVAILABLE, REOPEN,
           ACTIVATION, ACTIVATED, ACTIVATE_BUTTON, OPEN_BOT, RESUME, REPLIES_PAUSED,
           HELLO, THANKS, CHAT_HELP, CHAT_REGISTER, CHAT_EXISTING, CHAT_UID, CHAT_SIGNALS,
           CHAT_PAYMENT, CHAT_ASK, CHAT_PRIVACY, CHAT_BASIC, CHAT_PAUSED, CHAT_VOICE, CHAT_UNAVAILABLE, CHAT_NOTICE)


def text(source, language):
    # Explicit translation also works for combined paragraphs and business chats.
    return localization.translate_parts(source, language)


def prompt(user, reason):
    language = user.get('language', 'en')
    body = {
        'uid': journey.UNLINKED,
        'temporary': journey.PENDING,
        'registration': UID,
        'problem': journey.TEXTS['support'],
        'ready': journey.READY,
    }.get(reason, QUESTION)
    if reason == 'deposit' and user.get('access_check_deposit') is not None:
        body = journey.deposit(user['access_check_deposit'])[0]
    return text(INTRO, language) + '\n\n' + localization.translate_message(body, language)
