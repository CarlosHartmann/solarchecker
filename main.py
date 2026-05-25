from access_mails import connect_to_protonmail, retrieve_newest_email
from process_mail import process

def main():
    protonmail = connect_to_protonmail()
    try:
        email = retrieve_newest_email(protonmail)
        process(email)
    finally:
        protonmail.logout()

if __name__ == "__main__":
    main()