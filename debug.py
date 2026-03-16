import webuntis

session = webuntis.Session(
    school="oszimt",
    server="oszimt.webuntis.com",
    username="schulz_oskar",
    password="Gonkgta5?osz",
    useragent="debug/1.0",
)

session.login()

for k in session.klassen():
    if "fi" in k.name.lower() or "54" in k.name:
        print(f"  {k.id}: '{k.name}'")

session.logout()
