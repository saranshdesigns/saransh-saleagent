"""
Quick local test — simulate WhatsApp conversations without Meta API
Run: python test_agent.py
"""

from agent.core import process_message, process_owner_command
from agent.conversation import reset_conversation

TEST_PHONE = "919999999999"  # Fake client number for testing


def test_conversation(messages: list, reset: bool = True):
    """Run a test conversation and print responses."""
    if reset:
        reset_conversation(TEST_PHONE)

    print("=" * 60)
    for msg in messages:
        print(f"\n👤 Client: {msg}")
        reply = process_message(TEST_PHONE, msg)
        print(f"🤖 Agent: {reply}")
        print("-" * 40)


def test_logo_flow():
    print("\n🧪 TEST: Logo Sales Flow")
    test_conversation([
        "I am interested in logo design",
        "Defense, a mobile cover brand",
        "No tagline",
        "Icon + text style",
        "No reference",
        "Okay sounds good",
        "What is the price?",
        "Can you do it for 2500?",
        "Okay I agree, let's proceed"
    ])


def test_packaging_flow():
    print("\n🧪 TEST: Packaging - Juice Bottle (should detect LABEL, not pouch)")
    test_conversation([
        "I need packaging design for my juice bottle",
        "Brand name is Bindaas, juice bottle 250ml",
        "No logo yet",
        "Company name Fresh Sips, Mumbai",
        "How much will it cost?",
        "Okay let's proceed"
    ])


def test_owner_commands():
    print("\n🧪 TEST: Owner Commands")
    commands = [
        "Change logo price to 2799",
        "Reply like this from now: 'Our team will handle this professionally.'",
        "Don't answer questions about competitor pricing"
    ]
    for cmd in commands:
        print(f"\n👑 Owner: {cmd}")
        reply = process_owner_command(cmd)
        print(f"🤖 Agent: {reply}")


if __name__ == "__main__":
    print("SaranshDesigns AI Agent — Test Mode")
    print("=" * 60)

    choice = input("\nWhich test?\n1. Logo flow\n2. Packaging flow\n3. Owner commands\n4. All\n\nEnter (1-4): ").strip()

    if choice == "1":
        test_logo_flow()
    elif choice == "2":
        test_packaging_flow()
    elif choice == "3":
        test_owner_commands()
    elif choice == "4":
        test_logo_flow()
        test_packaging_flow()
        test_owner_commands()
    else:
        print("Running logo flow test by default...")
        test_logo_flow()
