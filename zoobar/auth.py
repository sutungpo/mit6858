from zoodb import *
from debug import *

import hashlib
import random
import pbkdf2

def newtoken(db, cred):
    hashinput = "%s%.10f" % (cred.password, random.random())
    cred.token = hashlib.md5(hashinput.encode('utf-8')).hexdigest()
    db.commit()
    return cred.token

def login(username, password):
    db = cred_setup()
    person = db.query(Cred).get(username)
    if not person:
        return None
    if person.password == pbkdf2.PBKDF2(password, person.salt).hexread(32):
        return newtoken(db, person)
    else:
        return None

def register(username, password):
#    db = person_setup()
    db_c = cred_setup()
#    person = db_c.query(Cred).get(username)
#    if person:
#        return None
#    newperson = Person()
    newcred = Cred()
#    newperson.username = username
    newcred.username = username
    salt = os.urandom(16)
    newcred.password = pbkdf2.PBKDF2(password, salt).hexread(32)
    newcred.salt = salt
#    db.add(newperson)
    db_c.add(newcred)
#    db.commit()
    db_c.commit()
    return newtoken(db_c, newcred)

def check_token(username, token):
    db = cred_setup()
    person = db.query(Cred).get(username)
    if person and person.token == token:
        return True
    else:
        return False

    
