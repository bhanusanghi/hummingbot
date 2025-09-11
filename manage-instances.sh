#!/bin/bash

# Management script for multiple Hummingbot instances

case "$1" in
    "setup")
        echo "Setting up instances..."
        ./setup-multi-instances.sh
        ;;
    "build")
        echo "Building Docker images from local source..."
        docker-compose -f docker-compose-bot1.yml build
        docker-compose -f docker-compose-bot2.yml build
        ;;
    "start")
        if [ -z "$2" ]; then
            echo "Starting all instances..."
            docker-compose -f docker-compose-bot1.yml up -d
            docker-compose -f docker-compose-bot2.yml up -d
        elif [ "$2" = "1" ] || [ "$2" = "bot1" ]; then
            echo "Starting bot1..."
            docker-compose -f docker-compose-bot1.yml up -d
        elif [ "$2" = "2" ] || [ "$2" = "bot2" ]; then
            echo "Starting bot2..."
            docker-compose -f docker-compose-bot2.yml up -d
        fi
        ;;
    "stop")
        if [ -z "$2" ]; then
            echo "Stopping all instances..."
            docker-compose -f docker-compose-bot1.yml down
            docker-compose -f docker-compose-bot2.yml down
        elif [ "$2" = "1" ] || [ "$2" = "bot1" ]; then
            echo "Stopping bot1..."
            docker-compose -f docker-compose-bot1.yml down
        elif [ "$2" = "2" ] || [ "$2" = "bot2" ]; then
            echo "Stopping bot2..."
            docker-compose -f docker-compose-bot2.yml down
        fi
        ;;
    "restart")
        if [ -z "$2" ]; then
            echo "Restarting all instances..."
            docker-compose -f docker-compose-bot1.yml down
            docker-compose -f docker-compose-bot2.yml down
            docker-compose -f docker-compose-bot1.yml up -d
            docker-compose -f docker-compose-bot2.yml up -d
        elif [ "$2" = "1" ] || [ "$2" = "bot1" ]; then
            echo "Restarting bot1..."
            docker-compose -f docker-compose-bot1.yml down
            docker-compose -f docker-compose-bot1.yml up -d
        elif [ "$2" = "2" ] || [ "$2" = "bot2" ]; then
            echo "Restarting bot2..."
            docker-compose -f docker-compose-bot2.yml down
            docker-compose -f docker-compose-bot2.yml up -d
        fi
        ;;
    "logs")
        if [ -z "$2" ]; then
            echo "Showing logs for all instances..."
            echo "=== Bot1 Logs ==="
            docker-compose -f docker-compose-bot1.yml logs --tail=50
            echo ""
            echo "=== Bot2 Logs ==="
            docker-compose -f docker-compose-bot2.yml logs --tail=50
        elif [ "$2" = "1" ] || [ "$2" = "bot1" ]; then
            docker-compose -f docker-compose-bot1.yml logs -f
        elif [ "$2" = "2" ] || [ "$2" = "bot2" ]; then
            docker-compose -f docker-compose-bot2.yml logs -f
        fi
        ;;
    "status")
        echo "Instance status:"
        echo ""
        echo "Bot1 (hummingbot-bot1):"
        docker-compose -f docker-compose-bot1.yml ps
        echo ""
        echo "Bot2 (hummingbot-bot2):"
        docker-compose -f docker-compose-bot2.yml ps
        ;;
    "attach")
        if [ "$2" = "1" ] || [ "$2" = "bot1" ]; then
            echo "Attaching to bot1 (use Ctrl+P Ctrl+Q to detach)..."
            docker attach hummingbot-bot1
        elif [ "$2" = "2" ] || [ "$2" = "bot2" ]; then
            echo "Attaching to bot2 (use Ctrl+P Ctrl+Q to detach)..."
            docker attach hummingbot-bot2
        else
            echo "Usage: $0 attach [1|2|bot1|bot2]"
        fi
        ;;
    "shell")
        if [ "$2" = "1" ] || [ "$2" = "bot1" ]; then
            echo "Opening shell for bot1..."
            docker exec -it hummingbot-bot1 bash -l
        elif [ "$2" = "2" ] || [ "$2" = "bot2" ]; then
            echo "Opening shell for bot2..."
            docker exec -it hummingbot-bot2 bash -l
        else
            echo "Usage: $0 shell [1|2|bot1|bot2]"
        fi
        ;;
    "clean")
        echo "Cleaning up instances..."
        docker-compose -f docker-compose-bot1.yml down -v
        docker-compose -f docker-compose-bot2.yml down -v
        docker system prune -f
        ;;
    *)
        echo "Hummingbot Multi-Instance Manager"
        echo ""
        echo "Usage: $0 {setup|build|start|stop|restart|logs|status|attach|shell|clean} [instance]"
        echo ""
        echo "Commands:"
        echo "  setup       - Initialize instance directories"
        echo "  build       - Build Docker images from local source"
        echo "  start       - Start instances (optionally specify 1 or 2)"
        echo "  stop        - Stop instances (optionally specify 1 or 2)"
        echo "  restart     - Restart instances (optionally specify 1 or 2)"
        echo "  logs        - Show logs (optionally specify 1 or 2)"
        echo "  status      - Show instance status"
        echo "  attach      - Attach to running Hummingbot (specify 1 or 2)"
        echo "  shell       - Open shell for debugging (specify 1 or 2)"
        echo "  clean       - Clean up containers and volumes"
        echo ""
        echo "Examples:"
        echo "  $0 setup                    # First time setup"
        echo "  $0 build                    # Build from local source"
        echo "  $0 start                    # Start both instances"
        echo "  $0 start 1                  # Start only bot1"
        echo "  $0 attach 1                 # Connect to bot1 Hummingbot"
        echo "  $0 logs 2                   # View bot2 logs"
        echo "  $0 stop                     # Stop all instances"
        echo ""
        echo "Note: Use 'attach' to connect to the running Hummingbot interface."
        echo "      Use Ctrl+P Ctrl+Q to detach without stopping the bot."
        ;;
esac
