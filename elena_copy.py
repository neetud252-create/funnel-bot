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
    'Pocket IQ will automatically show your menu here after you send the message.')
ACTIVATED = (
    '✅ <b>Your Pocket IQ account has been activated successfully!</b>\n\n'
    'Your menu is opening in @goplusaitrader_bot. Tap the username to return.\n\n'
    'I’m Elena, your Pocket IQ personal assistant. I can help you with setup.\n'
    'Replies in this chat are automated. Account verification and signal access requirements still apply.')
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
SOURCES = (INTRO, QUESTION, INVITE, PAUSED, STOPPED, HUMAN, OFFLINE, UID, FALLBACK,
           CONSENT, AGREE, BUTTONS_ONLY, LISTEN, AI_VOICE, VOICE_UNAVAILABLE, REOPEN,
           ACTIVATION, ACTIVATED, ACTIVATE_BUTTON, OPEN_BOT)


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
