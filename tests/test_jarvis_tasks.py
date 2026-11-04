"""
tests/test_jarvis_tasks.py - Regression Test Suite for Jarvis M2 Task Orchestrator

Validates all 17 required scenarios:
1. Single-step task
2. Multi-step task
3. Invalid tool rejection
4. Invalid arguments rejection
5. Dependency ordering
6. Failure prevents dependent execution
7. Partial completion
8. Approval pauses execution
9. Approval resumes exact pending step
10. Rejection cancels pending action
11. Task cancellation
12. Duplicate step prevention
13. Maximum execution-step limit
14. Normal conversation does not create a task
15. Existing direct commands continue working
16. M1 policy cannot be bypassed through orchestrator
17. Unsupported operations are reported honestly
"""

import unittest
from unittest.mock import patch, MagicMock

import jarvis_brain
import jarvis_policy
import jarvis_tasks
import jarvis_tools
import jarvis_windows
from jarvis_tasks import (
    MAX_TASK_STEPS,
    StepStatus,
    Task,
    TaskManager,
    TaskStatus,
    TaskStep,
    create_task_from_objective,
    execute_task,
    generate_task_report,
    is_multistep_objective,
    validate_plan,
)


class JarvisTaskOrchestratorTests(unittest.TestCase):
    def setUp(self):
        jarvis_policy.clear_pending_approval()
        jarvis_tasks.task_manager.clear_active_task()

    def tearDown(self):
        jarvis_policy.clear_pending_approval()
        jarvis_tasks.task_manager.clear_active_task()

    # 1. Single-step task
    @patch("jarvis_tools.execute_tool")
    def test_01_single_step_task(self, mock_exec):
        mock_exec.return_value = jarvis_windows.ToolResult(
            ok=True,
            message="Launched Notepad successfully.",
            data={"pid": 1234, "source": "system"},
        )

        task = Task(
            task_id="task_1",
            objective="open notepad",
            status=TaskStatus.READY,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Launch notepad",
                    tool_name="launch_application",
                    arguments={"name": "notepad"},
                )
            ],
        )

        report = execute_task(task)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(task.steps[0].verification_status, "VERIFIED")
        self.assertIn("Task completed successfully", report)
        self.assertIn("Step 1: Launch notepad", report)
        mock_exec.assert_called_once_with("launch_application", {"name": "notepad"}, approved=False)

    # 2. Multi-step task
    @patch("jarvis_tools.execute_tool")
    def test_02_multistep_task(self, mock_exec):
        mock_exec.side_effect = [
            jarvis_windows.ToolResult(ok=True, message="Launched Notepad.", data={"pid": 100}),
            jarvis_windows.ToolResult(
                ok=True,
                message="Typed 5 characters into Notepad.",
                data={"characters": 5, "window": "Notepad"},
            ),
        ]

        task = Task(
            task_id="task_2",
            objective="open notepad and type hello in notepad",
            status=TaskStatus.READY,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Launch notepad",
                    tool_name="launch_application",
                    arguments={"name": "notepad"},
                ),
                TaskStep(
                    step_id="step_2",
                    description="Type text into notepad",
                    tool_name="type_text",
                    arguments={"text": "hello", "expected_window": "notepad"},
                    dependencies=["step_1"],
                ),
            ],
        )

        report = execute_task(task)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(task.steps[1].status, StepStatus.COMPLETED)
        self.assertEqual(task.steps[1].verification_status, "VERIFIED")
        self.assertEqual(mock_exec.call_count, 2)
        self.assertIn("Step 1: Launch notepad", report)
        self.assertIn("Step 2: Type text into notepad", report)

    # 3. Invalid tool rejection
    def test_03_invalid_tool_rejection(self):
        steps = [
            TaskStep(
                step_id="step_1",
                description="Run arbitrary bash",
                tool_name="arbitrary_shell_command",
                arguments={"cmd": "dir"},
            )
        ]
        is_valid, msg = validate_plan(steps)
        self.assertFalse(is_valid)
        self.assertIn("not a registered desktop tool", msg)

    # 4. Invalid arguments rejection
    def test_04_invalid_arguments_rejection(self):
        # Wrong argument types
        steps_wrong_type = [
            TaskStep(
                step_id="step_1",
                description="Launch notepad with invalid argument type",
                tool_name="launch_application",
                arguments={"name": 9999},  # name should be str
            )
        ]
        is_valid, msg = validate_plan(steps_wrong_type)
        self.assertFalse(is_valid)
        self.assertIn("must be of type str", msg)

        # Missing required argument
        steps_missing_arg = [
            TaskStep(
                step_id="step_1",
                description="Launch without arguments",
                tool_name="launch_application",
                arguments={},
            )
        ]
        is_valid, msg = validate_plan(steps_missing_arg)
        self.assertFalse(is_valid)
        self.assertIn("do not match tool schema", msg)

    # 5. Dependency ordering
    def test_05_dependency_ordering(self):
        # Step depends on non-existent or forward step
        steps = [
            TaskStep(
                step_id="step_1",
                description="Type text",
                tool_name="type_text",
                arguments={"text": "hi", "expected_window": "notepad"},
                dependencies=["step_2"],  # forward dependency
            ),
            TaskStep(
                step_id="step_2",
                description="Launch notepad",
                tool_name="launch_application",
                arguments={"name": "notepad"},
            ),
        ]
        is_valid, msg = validate_plan(steps)
        self.assertFalse(is_valid)
        self.assertIn("does not precede it", msg)

    # 6. Failure prevents dependent execution
    @patch("jarvis_tools.execute_tool")
    def test_06_failure_prevents_dependent_execution(self, mock_exec):
        mock_exec.return_value = jarvis_windows.ToolResult(
            ok=False, message="Executable not found: fake_app"
        )

        task = Task(
            task_id="task_fail",
            objective="open fake_app and type hello in fake_app",
            status=TaskStatus.READY,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Launch fake_app",
                    tool_name="launch_application",
                    arguments={"name": "fake_app"},
                ),
                TaskStep(
                    step_id="step_2",
                    description="Type hello",
                    tool_name="type_text",
                    arguments={"text": "hello", "expected_window": "fake_app"},
                    dependencies=["step_1"],
                ),
            ],
        )

        report = execute_task(task)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.steps[0].status, StepStatus.FAILED)
        self.assertEqual(task.steps[1].status, StepStatus.SKIPPED)
        self.assertEqual(mock_exec.call_count, 1)  # step_2 was NOT executed
        self.assertIn("Skipped due to prerequisite failure", report)

    # 7. Partial completion
    @patch("jarvis_tools.execute_tool")
    def test_07_partial_completion(self, mock_exec):
        mock_exec.side_effect = [
            jarvis_windows.ToolResult(ok=True, message="Launched app.", data={"pid": 1}),
            jarvis_windows.ToolResult(ok=False, message="Foreground window not found."),
        ]

        task = Task(
            task_id="task_partial",
            objective="open notepad and type text in notepad",
            status=TaskStatus.READY,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Launch notepad",
                    tool_name="launch_application",
                    arguments={"name": "notepad"},
                ),
                TaskStep(
                    step_id="step_2",
                    description="Type text",
                    tool_name="type_text",
                    arguments={"text": "data", "expected_window": "notepad"},
                    dependencies=["step_1"],
                ),
                TaskStep(
                    step_id="step_3",
                    description="Send shortcut",
                    tool_name="send_shortcut",
                    arguments={"shortcut": "ctrl+s"},
                    dependencies=["step_2"],
                ),
            ],
        )

        report = execute_task(task)
        self.assertEqual(task.status, TaskStatus.PARTIALLY_COMPLETED)
        self.assertEqual(task.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(task.steps[1].status, StepStatus.FAILED)
        self.assertEqual(task.steps[2].status, StepStatus.SKIPPED)
        self.assertEqual(mock_exec.call_count, 2)
        self.assertIn("Task partially completed", report)

    # 8. Approval pauses execution
    @patch("jarvis_tools.execute_tool")
    def test_08_approval_pauses_execution(self, mock_exec):
        mock_exec.return_value = jarvis_windows.ToolResult(ok=True, message="App launched.")

        task = Task(
            task_id="task_appr",
            objective="open notepad and then close notepad",
            status=TaskStatus.READY,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Launch notepad",
                    tool_name="launch_application",
                    arguments={"name": "notepad"},
                ),
                TaskStep(
                    step_id="step_2",
                    description="Close application notepad",
                    tool_name="close_application",
                    arguments={"name": "notepad"},
                    dependencies=["step_1"],
                ),
            ],
        )

        resp = execute_task(task)
        self.assertEqual(task.status, TaskStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(task.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(task.steps[1].status, StepStatus.WAITING_FOR_APPROVAL)
        self.assertTrue(jarvis_policy.has_pending_approval())
        self.assertIn("requires your confirmation", resp)
        self.assertEqual(mock_exec.call_count, 1)  # step_2 was NOT executed yet

    # 9. Approval resumes exact pending step
    @patch("jarvis_tools.execute_tool")
    def test_09_approval_resumes_exact_pending_step(self, mock_exec):
        mock_exec.side_effect = [
            jarvis_windows.ToolResult(ok=True, message="App launched.", data={"pid": 1}),
            jarvis_windows.ToolResult(ok=True, message="Closed notepad.", data={"app": "notepad"}),
        ]

        task = Task(
            task_id="task_resume",
            objective="open notepad and then close notepad",
            status=TaskStatus.READY,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Launch notepad",
                    tool_name="launch_application",
                    arguments={"name": "notepad"},
                ),
                TaskStep(
                    step_id="step_2",
                    description="Close application notepad",
                    tool_name="close_application",
                    arguments={"name": "notepad"},
                    dependencies=["step_1"],
                ),
            ],
        )
        jarvis_tasks.task_manager.set_active_task(task)

        # Step 1 executes, pauses at Step 2 for approval
        execute_task(task)
        self.assertEqual(task.status, TaskStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(task.current_step_index, 1)

        # Resume with explicit approval
        resp = jarvis_tasks.task_manager.resume_active_task_with_approval()
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertEqual(task.steps[1].status, StepStatus.COMPLETED)
        self.assertEqual(task.steps[1].verification_status, "VERIFIED")
        self.assertIn("Task completed successfully", resp)
        # Verify second call had approved=True
        mock_exec.assert_called_with("close_application", {"name": "notepad"}, approved=True)

    # 10. Rejection cancels pending action
    @patch("jarvis_tools.execute_tool")
    def test_10_rejection_cancels_pending_action(self, mock_exec):
        mock_exec.return_value = jarvis_windows.ToolResult(ok=True, message="App launched.", data={"pid": 1})

        task = Task(
            task_id="task_reject",
            objective="open notepad and then close notepad",
            status=TaskStatus.READY,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Launch notepad",
                    tool_name="launch_application",
                    arguments={"name": "notepad"},
                ),
                TaskStep(
                    step_id="step_2",
                    description="Close application notepad",
                    tool_name="close_application",
                    arguments={"name": "notepad"},
                    dependencies=["step_1"],
                ),
            ],
        )
        jarvis_tasks.task_manager.set_active_task(task)
        execute_task(task)

        # User rejects via think()
        resp = jarvis_brain.think("no")
        self.assertEqual(resp, "Okay, I cancelled that action.")
        self.assertFalse(jarvis_policy.has_pending_approval())
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(task.steps[1].status, StepStatus.CANCELLED)
        self.assertEqual(mock_exec.call_count, 1)  # close_application was never executed

    # 11. Task cancellation
    def test_11_task_cancellation(self):
        task = Task(
            task_id="task_cancel",
            objective="open notepad and type hello",
            status=TaskStatus.RUNNING,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Launch notepad",
                    tool_name="launch_application",
                    arguments={"name": "notepad"},
                    status=StepStatus.PENDING,
                )
            ],
        )
        jarvis_tasks.task_manager.set_active_task(task)
        cancelled = jarvis_tasks.task_manager.cancel_active_task()
        self.assertIsNotNone(cancelled)
        self.assertEqual(cancelled.status, TaskStatus.CANCELLED)
        self.assertIsNone(jarvis_tasks.task_manager.get_active_task())

        # Also via brain voice command
        task2 = Task(
            task_id="task_cancel_2",
            objective="open notepad and type hello",
            status=TaskStatus.RUNNING,
        )
        jarvis_tasks.task_manager.set_active_task(task2)
        resp = jarvis_brain.think("cancel task")
        self.assertIn("Task cancelled", resp)

    # 12. Duplicate step prevention
    @patch("jarvis_tools.execute_tool")
    def test_12_duplicate_step_prevention(self, mock_exec):
        mock_exec.return_value = jarvis_windows.ToolResult(ok=True, message="Typed text.")

        step1 = TaskStep(
            step_id="step_1",
            description="Launch notepad",
            tool_name="launch_application",
            arguments={"name": "notepad"},
            status=StepStatus.COMPLETED,  # already completed
            verification_status="VERIFIED",
        )
        step2 = TaskStep(
            step_id="step_2",
            description="Type text",
            tool_name="type_text",
            arguments={"text": "hello", "expected_window": "notepad"},
            dependencies=["step_1"],
            status=StepStatus.PENDING,
        )

        task = Task(
            task_id="task_dup",
            objective="test duplicate prevention",
            status=TaskStatus.READY,
            steps=[step1, step2],
            current_step_index=0,
        )

        execute_task(task)
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        # execute_tool should only be called ONCE for step_2, never for step_1
        self.assertEqual(mock_exec.call_count, 1)
        mock_exec.assert_called_once_with("type_text", {"text": "hello", "expected_window": "notepad"}, approved=False)

    # 13. Maximum execution-step limit
    def test_13_maximum_execution_step_limit(self):
        # Plan exceeding 10 steps is rejected
        oversized_steps = [
            TaskStep(
                step_id=f"step_{i}",
                description=f"Step {i}",
                tool_name="search_web",
                arguments={"query": f"test_{i}"},
            )
            for i in range(1, MAX_TASK_STEPS + 2)
        ]
        is_valid, msg = validate_plan(oversized_steps)
        self.assertFalse(is_valid)
        self.assertIn(f"exceeds maximum limit of {MAX_TASK_STEPS}", msg)

    # 14. Normal conversation does not create a task
    def test_14_normal_conversation_does_not_create_task(self):
        conversational_phrases = [
            "What is the capital of France?",
            "Tell me a joke.",
            "How are you today?",
            "Can you explain how async works in Python?",
            "Who won the world cup in 2022?",
        ]
        for phrase in conversational_phrases:
            self.assertFalse(
                is_multistep_objective(phrase),
                f"Phrase '{phrase}' should not be classified as a multi-step objective",
            )

    # 15. Existing direct commands continue working
    def test_15_existing_direct_commands_continue_working(self):
        direct_commands = [
            "open notepad",
            "close notepad",
            "type hello in notepad",
            "search Python on youtube",
            "google weather in Tokyo",
            "press alt+tab",
        ]
        for cmd in direct_commands:
            self.assertFalse(
                is_multistep_objective(cmd),
                f"Single command '{cmd}' should not trigger multi-step orchestrator",
            )

    # 16. M1 policy cannot be bypassed through orchestrator
    @patch("jarvis_tools.execute_tool")
    def test_16_m1_policy_cannot_be_bypassed_through_orchestrator(self, mock_exec):
        # A plan trying to execute close_application must NOT execute without approval
        task = Task(
            task_id="task_sec",
            objective="close notepad",
            status=TaskStatus.READY,
            steps=[
                TaskStep(
                    step_id="step_1",
                    description="Close application notepad",
                    tool_name="close_application",
                    arguments={"name": "notepad"},
                )
            ],
        )

        resp = execute_task(task, resume_step_approved=False)
        self.assertEqual(task.status, TaskStatus.WAITING_FOR_APPROVAL)
        self.assertIn("requires your confirmation", resp)
        # Verify tool was NOT executed
        mock_exec.assert_not_called()

    # 17. Unsupported operations are reported honestly
    def test_17_unsupported_operations_reported_honestly(self):
        unsupported_prompt = (
            "Open Notepad, write a Java program to reverse a string, and save it on my desktop."
        )
        self.assertTrue(is_multistep_objective(unsupported_prompt))

        task = create_task_from_objective(unsupported_prompt)
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIn("saving files or creating files directly on disk is not supported yet", task.outcome)


if __name__ == "__main__":
    unittest.main()
