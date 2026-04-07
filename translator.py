from deep_translator import GoogleTranslator

def traduci(testo: str) -> str:
    if not testo:
        return ""
    try:
        return GoogleTranslator(source="en", target="it").translate(testo[:5000])
    except Exception as e:
        print(f"Errore traduzione: {e}")
        return testo  # fallback: testo originale

if __name__ == "__main__":
    testo = "Plunge into the depths where crushing weight meets chilling fragility. The latest Brutal Assault update expands the line-up with a total of 12 bands. From the nautical funerals of Ahab and the sludge anthems of the legendary Crowbar, to the post-metal filth of local icons LVMEN and the total sonic terror of Violent Magic Orchestra. Each of these acts defines a distinct shade of darkness. At the same time, we regret to announce that the American outfit Fallujah will not be performing at this year's Brutal. Taking their slot on the line-up are their British peers, Cryptic Shift."
    tradotto = traduci(testo)
    print(tradotto)