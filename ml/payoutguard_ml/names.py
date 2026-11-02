"""Name pools and nickname tables used by the generator and (partly) by the features.

NICKNAMES_GEN is what the generator can produce. NICKNAMES_FEAT is the SUBSET the matcher is
allowed to know about. Nickname aliases outside the subset test generalisation; they are not
a free signal. This limits (does not remove) circularity between generator and model.
"""

FIRST_NAMES = """Aarav Aditya Ananya Anil Anita Arjun Deepa Divya Gaurav Isha Kavya Kiran Lakshmi Manish
Meera Mohan Neha Nikhil Pooja Pradeep Priya Rahul Rajesh Ravi Rohan Sanjay Shreya Sunita Suresh Vikram
Vivek Zoya Aisha Fatima Omar Yusuf Layla Hassan Noor Tariq James John Robert Michael William David
Richard Joseph Thomas Charles Christopher Daniel Matthew Anthony Elizabeth Jennifer Margaret Katherine
Susan Jessica Sarah Karen Nancy Alexander Benjamin Samuel Nicholas Patrick Stephen Jonathan Andrew
Emily Hannah Olivia Sophia Isabella Charlotte Amelia Grace Chloe Lucas Mateo Santiago Diego Sofia
Valentina Camila Lucia Carlos Miguel Javier Elena Marta Jose Pedro Andre Joao Ines Mariana Luca
Matteo Giulia Chiara Lena Jonas Felix Anna Hans Klara Mei Wei Li Yuki Haruto Sakura Hiro Min Jun
Seo Ji Tomasz Piotr Katarzyna Ivan Olga Dmitri Natalia Sven Astrid Lars Freja Liam Noah Ethan Mason""".split()

LAST_NAMES = """Sharma Verma Gupta Singh Kumar Patel Reddy Nair Iyer Menon Rao Joshi Mehta Shah Desai Kapoor
Malhotra Chopra Banerjee Chatterjee Mukherjee Das Bose Khan Ahmed Ali Hussain Siddiqui Smith Johnson
Williams Brown Jones Miller Davis Wilson Anderson Taylor Moore Jackson Martin Lee Thompson White Harris
Clark Lewis Robinson Walker Young Allen King Wright Scott Green Baker Adams Nelson Hill Campbell
Mitchell Roberts Carter Phillips Evans Turner Torres Parker Collins Edwards Stewart Morris Murphy
Cook Rogers Morgan Cooper Peterson Reed Bailey Bell Kelly Howard Ward Cox Richardson Wood Watson
Garcia Rodriguez Martinez Hernandez Lopez Gonzalez Perez Sanchez Ramirez Flores Silva Santos Oliveira
Souza Costa Rossi Russo Ferrari Esposito Bianchi Muller Schmidt Schneider Fischer Weber Meyer Wagner
Becker Hoffmann Tanaka Suzuki Sato Watanabe Yamamoto Nakamura Kim Park Choi Chen Wang Zhang Liu Yang
Nowak Kowalski Ivanov Petrov Smirnov Andersson Johansson Nilsson Larsen Hansen""".split()

# Full table the generator may use: short form -> formal form.
NICKNAMES_GEN = {
    "Bob": "Robert", "Rob": "Robert", "Bill": "William", "Will": "William", "Jim": "James",
    "Mike": "Michael", "Dave": "David", "Rick": "Richard", "Dick": "Richard", "Joe": "Joseph",
    "Tom": "Thomas", "Chris": "Christopher", "Dan": "Daniel", "Matt": "Matthew", "Tony": "Anthony",
    "Liz": "Elizabeth", "Beth": "Elizabeth", "Jen": "Jennifer", "Maggie": "Margaret", "Kate": "Katherine",
    "Sue": "Susan", "Jess": "Jessica", "Alex": "Alexander", "Ben": "Benjamin", "Sam": "Samuel",
    "Nick": "Nicholas", "Pat": "Patrick", "Steve": "Stephen", "Jon": "Jonathan", "Andy": "Andrew",
    "Manny": "Manish", "Vik": "Vikram", "Raj": "Rajesh", "Sanj": "Sanjay", "Pri": "Priya",
}
# Subset known to the matcher's features (about two thirds).
_FEAT_KEYS = ["Bob", "Rob", "Bill", "Will", "Jim", "Mike", "Dave", "Rick", "Joe", "Tom", "Chris",
              "Dan", "Matt", "Tony", "Liz", "Jen", "Kate", "Sue", "Alex", "Ben", "Sam", "Nick"]
NICKNAMES_FEAT = {k: NICKNAMES_GEN[k] for k in _FEAT_KEYS}

BIZ_SUFFIXES = ["Studio", "Media", "Labs", "Design", "Consulting", "Creative", "Works", "Digital"]
BIZ_LEGAL = ["LLC", "Inc", "Ltd", "Co"]

FREE_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "proton.me", "icloud.com"]
COUNTRIES = ["IN", "US", "GB", "DE", "BR", "CA", "AU", "FR", "ES", "JP"]
COUNTRY_P = [0.28, 0.24, 0.10, 0.07, 0.06, 0.06, 0.05, 0.05, 0.05, 0.04]
