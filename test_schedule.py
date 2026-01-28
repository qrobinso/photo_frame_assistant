import sys
from server import app, db, ScheduledGeneration, execute_generation

def test_schedule(schedule_id):
    """Test a scheduled generation immediately."""
    try:
        # Get the schedule
        schedule = db.session.get(ScheduledGeneration, schedule_id)
        if not schedule:
            print(f"Schedule {schedule_id} not found")
            return

        print(f"Testing schedule: {schedule.name} (ID: {schedule_id})")
        print(f"Service: {schedule.service}")
        print(f"Frame: {schedule.frame_id}")
        
        # Execute the generation
        result = execute_generation(schedule_id)
        print("Test completed successfully")
        return result

    except Exception as e:
        print(f"Error testing schedule: {str(e)}")
        return None

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python test_schedule.py <schedule_id>")
        sys.exit(1)

    try:
        schedule_id = int(sys.argv[1])
    except ValueError:
        print("Schedule ID must be a number")
        sys.exit(1)

    with app.app_context():
        test_schedule(schedule_id) 