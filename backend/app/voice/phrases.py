"""Short spoken phrases in the member's language (brief §6.4: English + Hindi first, Tamil
stretch). Voice notes say these instead of reading out the full chat text, so a Hindi-speaking
parent hears Hindi. Unknown phrase/language combinations fall back to English.

Numbers stay as digits: the TTS model reads them in the voice's language.
"""

from app.conversation.messages import Voice

LANGS = ("en", "hi", "ta")

PHRASES: dict[str, dict[str, str]] = {
    "dose_reminder": {
        "en": "{time}: time for {names}. Reply 1 when you have taken it, or 2 if you skip it.",
        "hi": "{time}: {names} लेने का समय हो गया है। दवा ले लें तो 1 भेजिए, न लें तो 2।",
        "ta": "{time}: {names} எடுத்துக்கொள்ளும் நேரம். எடுத்துக்கொண்டதும் 1 அனுப்புங்கள், தவிர்த்தால் 2.",
    },
    "dose_followup": {
        "en": "Did you take {names} from {time}? Reply 1 if you have taken it.",
        "hi": "क्या आपने {time} वाली {names} ले ली? ले ली हो तो 1 भेजिए।",
        "ta": "{time} மணிக்கான {names} எடுத்துக்கொண்டீர்களா? எடுத்திருந்தால் 1 அனுப்புங்கள்.",
    },
    "dose_taken": {
        "en": "Noted, {names} taken. Thank you.",
        "hi": "ठीक है, {names} ले ली। धन्यवाद।",
        "ta": "சரி, {names} எடுத்துக்கொண்டதாக பதிவு செய்தேன். நன்றி.",
    },
    "dose_skipped": {
        "en": "Noted, {names} not taken.",
        "hi": "ठीक है, {names} नहीं ली।",
        "ta": "சரி, {names} எடுக்கவில்லை என்று பதிவு செய்தேன்.",
    },
    "read_back": {
        "en": "I heard {spoken}. Is that right? Reply 1 for yes, 2 for no.",
        "hi": "मैंने सुना: {spoken}। क्या यह सही है? हाँ के लिए 1, नहीं के लिए 2 भेजिए।",
        "ta": "நான் கேட்டது: {spoken}. இது சரியா? ஆம் என்றால் 1, இல்லை என்றால் 2.",
    },
    "saved": {
        "en": "Saved. Thank you.",
        "hi": "सेव कर लिया। धन्यवाद।",
        "ta": "சேமித்தேன். நன்றி.",
    },
    "low_stock": {
        "en": "Your {brand} is running low. I have asked your family to approve a refill.",
        "hi": "आपकी {brand} खत्म होने वाली है। मैंने परिवार से रीफ़िल मंज़ूर करने को कहा है।",
    },
    "refill_approved": {
        "en": "Your family approved the refill for {brand}. Your pharmacy will confirm.",
        "hi": "आपके परिवार ने {brand} की रीफ़िल मंज़ूर कर दी है। आपकी फ़ार्मेसी पुष्टि करेगी।",
    },
    "not_understood": {
        "en": "Sorry, I could not understand the voice note. Please say it again, or type it.",
        "hi": "माफ़ कीजिए, आवाज़ वाला संदेश समझ नहीं आया। कृपया फिर से बोलिए या लिखकर भेजिए।",
        "ta": "மன்னிக்கவும், குரல் செய்தி புரியவில்லை. மீண்டும் சொல்லுங்கள் அல்லது எழுதி அனுப்புங்கள்.",
    },
    "off_topic": {
        "en": "I can only help with readings and medicines.",
        "hi": "मैं सिर्फ़ रीडिंग और दवाइयों में मदद कर सकता हूँ।",
        "ta": "அளவீடுகள் மற்றும் மருந்துகளுக்கு மட்டுமே என்னால் உதவ முடியும்.",
    },
    # Severe BP: the warning-signs question (2025 AHA/ACC), and what to do on "yes".
    "symptom_check_bp": {
        "en": "Do you have chest pain, breathlessness, weakness or numbness on one side, "
        "slurred speech, a severe headache, confusion, or sudden vision problems right now? "
        "Reply 1 for yes, 2 for no.",
        "hi": "क्या अभी आपको सीने में दर्द, साँस फूलना, एक तरफ़ कमज़ोरी या सुन्नपन, बोलने में "
        "दिक्कत, तेज़ सिरदर्द, उलझन, या अचानक देखने में परेशानी है? हाँ के लिए 1, नहीं के लिए 2 "
        "भेजिए।",
        "ta": "இப்போது நெஞ்சு வலி, மூச்சுத் திணறல், ஒரு பக்கம் பலவீனம் அல்லது மரத்துப்போதல், "
        "பேச்சுக் குழறல், கடும் தலைவலி, குழப்பம், அல்லது திடீர் பார்வைப் பிரச்சனை உள்ளதா? "
        "ஆம் என்றால் 1, இல்லை என்றால் 2.",
    },
    "call_108": {
        "en": "Please call 108 or 112 now, or ask someone near you to call.",
        "hi": "कृपया अभी 108 या 112 पर फ़ोन करें, या पास के किसी व्यक्ति से फ़ोन करवाएँ।",
        "ta": "தயவுசெய்து இப்போதே 108 அல்லது 112 அழையுங்கள், அல்லது அருகில் உள்ளவரை அழைக்கச் சொல்லுங்கள்.",
    },
    "rest_repeat": {
        "en": "Please rest for 5 minutes, then measure again and send the new reading.",
        "hi": "कृपया 5 मिनट आराम करें, फिर दोबारा नापकर नई रीडिंग भेजें।",
        "ta": "5 நிமிடம் ஓய்வெடுத்து, மீண்டும் அளந்து புதிய அளவை அனுப்புங்கள்.",
    },
    "contact_doctor": {
        "en": "The repeat reading is still high. Please contact your doctor today.",
        "hi": "दोबारा की रीडिंग भी ज़्यादा है। कृपया आज ही अपने डॉक्टर से संपर्क करें।",
        "ta": "மீண்டும் எடுத்த அளவும் அதிகமாக உள்ளது. இன்றே உங்கள் மருத்துவரைத் தொடர்பு கொள்ளுங்கள்.",
    },
    # Low sugar: guidance is only the doctor's plan (sent as text); the voice says where it is.
    "symptom_check_low": {
        "en": "Did you faint, feel confused, or did someone have to help you? "
        "Reply 1 for yes, 2 for no.",
        "hi": "क्या आप बेहोश हुए, उलझन हुई, या किसी को आपकी मदद करनी पड़ी? हाँ के लिए 1, "
        "नहीं के लिए 2 भेजिए।",
        "ta": "நீங்கள் மயங்கினீர்களா, குழப்பமாக இருந்ததா, அல்லது யாராவது உதவ வேண்டியிருந்ததா? "
        "ஆம் என்றால் 1, இல்லை என்றால் 2.",
    },
    "low_sugar_plan": {
        "en": "Your sugar is low. Please follow your doctor's plan for low sugar, and check "
        "again in 15 minutes.",
        "hi": "आपकी शुगर कम है। कृपया कम शुगर के लिए अपने डॉक्टर की बताई योजना अपनाएँ, और 15 मिनट "
        "बाद दोबारा जाँचें।",
        "ta": "உங்கள் சர்க்கரை குறைவாக உள்ளது. குறைந்த சர்க்கரைக்கு உங்கள் மருத்துவரின் திட்டத்தைப் "
        "பின்பற்றி, 15 நிமிடத்தில் மீண்டும் சோதியுங்கள்.",
    },
    "low_sugar_help": {
        "en": "Please ask someone near you to help now, and call 108 or 112 if you feel faint "
        "or confused.",
        "hi": "कृपया अभी पास के किसी व्यक्ति से मदद लें, और बेहोशी या उलझन लगे तो 108 या 112 पर फ़ोन करें।",
        "ta": "அருகில் உள்ளவரிடம் இப்போதே உதவி கேளுங்கள்; மயக்கம் அல்லது குழப்பம் இருந்தால் 108 "
        "அல்லது 112 அழையுங்கள்.",
    },
    # Red-alert call to a family member. English carries the server's alert text; other
    # languages use a fixed sentence (no numbers) so nothing is mistranslated.
    "red_call": {
        "en": "This is Khayal with an urgent alert about {parent}. {reason} "
        "Please call {parent} now.",
        "hi": "यह Khayal की ज़रूरी सूचना है। {parent} की रीडिंग उनके डॉक्टर की तय सीमा से "
        "बाहर है। कृपया अभी {parent} को फ़ोन करें।",
    },
}


def lang_of(member_language: str | None) -> str:
    lang = (member_language or "en").lower()[:2]
    return lang if lang in LANGS else "en"


def say(key: str, lang: str, **params: object) -> Voice:
    """The phrase in `lang` (falls back to English), with params filled in."""
    lang = lang_of(lang)
    options = PHRASES[key]
    use = lang if lang in options else "en"
    return Voice(text=options[use].format(**params), lang=use)


def time_words(hhmm: str, lang: str) -> str:
    """ "20:00" -> "8:00 PM" (en) / "रात 8 बजे" (hi) / "20:00" (ta)."""
    h, m = map(int, hhmm.split(":"))
    lang = lang_of(lang)
    if lang == "hi":
        part = "सुबह" if 4 <= h < 12 else "दोपहर" if h < 16 else "शाम" if h < 20 else "रात"
        h12 = h % 12 or 12
        return f"{part} {h12}:{m:02d} बजे" if m else f"{part} {h12} बजे"
    if lang == "ta":
        return f"{h:02d}:{m:02d}"
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def spoken_reading(
    type_: str,
    lang: str,
    *,
    systolic: int | None = None,
    diastolic: int | None = None,
    glucose: str | None = None,
) -> str:
    lang = lang_of(lang)
    if type_ == "bp":
        return {
            "hi": f"BP ऊपर {systolic}, नीचे {diastolic}",
            "ta": f"BP மேல் {systolic}, கீழ் {diastolic}",
        }.get(lang, f"BP {systolic} over {diastolic}")
    return {"hi": f"शुगर {glucose}", "ta": f"சர்க்கரை {glucose}"}.get(lang, f"sugar {glucose}")
