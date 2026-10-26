"""Elina's local, translated setup help. No external AI request is made here."""
import localization
import journey

# ---- brand / offer ----
NAME = 'Elina'
PROMO_CODE = 'YEB789'
BONUS_MIN_DEPOSIT = 50
BONUS_PERCENT = 60

LINK_OPEN = 'Open Pocket IQ'
LINK_REGISTER = '🔗 Registration Link'

INTRO = 'Hi, I’m Elina 👋 Your personal manager at Pocket IQ.'
ACTIVATE_BUTTON = '💬 Message Elina to Activate'
OPEN_BOT = '🚀 Open Pocket IQ'
ACTIVATION = (
    '👋 <b>Welcome to Pocket IQ</b>\n\n'
    '<b>Activate your bot access</b>\n'
    'Message Elina, your personal manager, to get started.\n\n'
    'Tap the button below and press <b>Send</b> in Elina’s chat using this same Telegram account.\n\n'
    'Pocket IQ will automatically show your menu here after you send the message.')
ACTIVATED = (
    '✅ <b>Your Pocket IQ account is activated!</b>\n\n'
    'Your menu is opening in @goplusaitrader_bot. Tap the username to return.\n\n'
    'I’m Elina, your personal manager. Next step: register and finish your qualifying deposit to unlock full access.')
QUESTION = 'What would you like help with? Choose an option below.'
INVITE = 'Need help finishing your Pocket IQ setup? Chat with Elina or continue here.'
PAUSED = 'Follow-ups are paused. You can return whenever you are ready.'
STOPPED = 'Reminders are stopped. Your bot access is unchanged.'
HUMAN = ''          # removed: no "automated replies are paused" notice
OFFLINE = 'Elina’s automatic chat is not connected yet. You can continue setup in Pocket IQ.'
UID = 'Open your Pocket Option profile to find your Trader ID, then submit it inside Pocket IQ using the button below.'
FALLBACK = 'Choose a topic below, or request a person for help with your message.'
CONSENT = 'I’m Elina, your personal manager at Pocket IQ. With your permission, OpenAI receives your message, a short redacted conversation, language, setup status, verification result and qualifying-deposit amount to answer you. Please do not send passwords, login codes, payment details or Trader IDs. Redacted chat history is kept here for up to 24 hours. You can use help buttons or request a person instead. Voice is optional: tapping Listen sends the reply text to OpenAI to create an AI voice. Stop revokes permission and clears this chat memory.'
AGREE = 'Allow AI replies'
BUTTONS_ONLY = 'Use help buttons only'
LISTEN = '🔊 Listen · AI voice via OpenAI'
AI_VOICE = 'AI-generated voice · Elina, Pocket IQ'
VOICE_UNAVAILABLE = 'Voice is unavailable right now. You can read the reply above.'
REOPEN = 'Send Elina a new message to continue this chat.'
RESUME = 'Resume replies'
REPLIES_PAUSED = REOPEN
HELLO = 'Hi 👋 Ready to grab your 2 free analyses?'
THANKS = 'Anytime 🙌'
CHAT_HELP = 'Tell me what’s stuck in your setup and I’ll sort the next step.'
CHAT_REGISTER = 'Full access just needs a Pocket Option account registered through our bot link. Tap the link below, finish the broker’s form, then send your Trader ID in Pocket IQ. Takes 2 minutes.'
CHAT_EXISTING = 'Full access needs an account registered through our link. Send your current Trader ID in Pocket IQ to check it. If it isn’t linked, ping Pocket Option support before making another account.'
CHAT_UID = 'Copy the numeric Trader ID from your Pocket Option profile, then send it in Pocket IQ under Setup My Account. Rejected? Check for a typo and resend.'
CHAT_SIGNALS = 'In Pocket IQ tap Get Signals, then pick market, category, asset and expiry. You get a chart, BUY or SELL and a short reason — 95%+ accuracy on live signals.'
CHAT_ACCURACY = '95%+ accuracy on live signals 🔥'
CHAT_ACCURACY_2 = 'We only count live, confirmed signals — that’s why the hit rate stays that high.'
CHAT_PAYMENT = 'Payments and withdrawals are handled inside your Pocket Option account. If a payment is missing, recheck in Pocket IQ or contact broker support before depositing again.'
CHAT_ASK = 'May I use OpenAI for personalised replies with your message, recent redacted chat and setup, verification and qualifying-deposit details? Reply YES or NO. /privacy explains more.'
CHAT_PRIVACY = 'Elina’s replies use OpenAI with redacted messages, recent chat, language, setup, verification and qualifying-deposit details. Local chat memory expires after 24 hours. Never send passwords, login codes or payment details. /voice requests an AI voice; /basic uses local help; /human requests the team; /stop stops replies and clears local memory.'
CHAT_BASIC = 'Sure, I can still help with basic setup here. Send your question. You can type /ai later for personalised replies.'
CHAT_PAUSED = REOPEN
CHAT_VOICE = 'Ask a question, then send /voice to hear the latest reply in an AI voice.'
CHAT_NOTICE = ''     # removed the "replies are automated" note
CHAT_UNAVAILABLE = 'Personalised replies are unavailable right now. I can still help with basic setup.'

SOURCES = (INTRO, QUESTION, INVITE, PAUSED, STOPPED, OFFLINE, UID, FALLBACK,
           CONSENT, AGREE, BUTTONS_ONLY, LISTEN, AI_VOICE, VOICE_UNAVAILABLE, REOPEN,
           ACTIVATION, ACTIVATED, ACTIVATE_BUTTON, OPEN_BOT, RESUME, REPLIES_PAUSED,
           HELLO, THANKS, CHAT_HELP, CHAT_REGISTER, CHAT_EXISTING, CHAT_UID, CHAT_SIGNALS,
           CHAT_PAYMENT, CHAT_ASK, CHAT_PRIVACY, CHAT_BASIC, CHAT_PAUSED, CHAT_VOICE,
           CHAT_UNAVAILABLE, CHAT_NOTICE, LINK_OPEN, LINK_REGISTER,
           CHAT_ACCURACY, CHAT_ACCURACY_2)


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
    return text(INTRO, language) + '\n\n' + text(body, language)
