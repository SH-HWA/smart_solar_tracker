/* USER CODE BEGIN Header */
/**
 ******************************************************************************
 * @file           : main.c
 * @brief          : Main program body
 ******************************************************************************
 */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "i2c.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <stdio.h>
#include <string.h>
#include <stdlib.h> 
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define RX_BUFFER_SIZE 64  
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
uint8_t bh1750_cmd = 0x10;
uint8_t bh1750_data[2];
uint16_t raw;
float lux;
char msg[64];

/* UART 실시간 AI 데이터 수신용 변수들 */
uint8_t rx_data;                 // 1바이트 수신용 임시 변수
char rx_buffer[RX_BUFFER_SIZE];  // 수신 패킷을 모아둘 버퍼
uint8_t rx_index = 0;            // 버퍼 인덱스
uint8_t rx_flag = 0;             // 문장 수신 완료 플래그 (1이 되면 파싱 시작)
uint16_t target_angle = 90;      // 초기 각도는 중앙값(90도)으로 대기
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
void Servo_SetPosition(uint16_t pwm);
void Parse_AI_Command(char *packet);
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
/* UART 인터럽트 콜백 함수 정의
 파이썬 서버가 시리얼로 데이터를 보내면 하드웨어 인터럽트에 의해 이 함수가 자동 호출됩니다. */
void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
	if (huart->Instance == USART2)  // PC와 연결된 UART2 포트인 경우(포트 확인 필요)
	{
		if (rx_data == '\n' || rx_data == '\r') // 줄바꿈 문자가 들어왔다면 (한 문장 완료)
				{
			if (rx_index > 0) {
				rx_buffer[rx_index] = '\0'; // 문자열의 끝을 지정
				rx_flag = 1;                // 메인 루프에게 "파싱해라"라고 플래그 알림
			}
		} else // 일반 글자가 들어오는 중이라면 버퍼에 차곡차곡 축적
		{
			if (rx_index < RX_BUFFER_SIZE - 1) {
				rx_buffer[rx_index++] = rx_data;
			} else // 버퍼 오버플로우 방지 리셋
			{
				rx_index = 0;
			}
		}

		// 다음 1바이트 수신을 위해 인터럽트를 재가동합니다.
		HAL_UART_Receive_IT(&huart2, &rx_data, 1);
	}
}

/* 수신된 텍스트 패킷 해석 함수 */
void Parse_AI_Command(char *packet) {
	int parsed_angle = 0;
	HAL_GPIO_TogglePin(GPIOA, GPIO_PIN_5);
	// 만약 들어온 문자열이 "SERVO:"로 시작한다면 뒤의 정수값을 %d로 읽어옵니다.
	if (sscanf(packet, "SERVO:%d", &parsed_angle) == 1) {

		// 안전장치: 모터 가동 범위 규격 제한 (0도 ~ 180도)
		if (parsed_angle >= 0 && parsed_angle <= 180) {
			target_angle = (uint16_t) parsed_angle;

			// 테라텀 확인용 피드백 출력
			sprintf(msg, ">> [STM32 OK] AI 각도 적용 완료: %d도\r\n", target_angle);
			HAL_UART_Transmit(&huart2, (uint8_t*) msg, strlen(msg),
			HAL_MAX_DELAY);
		}
	}
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_I2C1_Init();
  MX_TIM1_Init();
  MX_USART2_UART_Init();
  /* USER CODE BEGIN 2 */
	HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);
	HAL_I2C_Master_Transmit(&hi2c1, 0x23 << 1, &bh1750_cmd, 1,
	HAL_MAX_DELAY);
	HAL_StatusTypeDef status;

	status = HAL_I2C_IsDeviceReady(&hi2c1, 0x23 << 1, 3, 100);

	sprintf(msg, "ADDR23 status=%d\r\n", status);

	HAL_UART_Transmit(&huart2, (uint8_t*) msg, strlen(msg),
	HAL_MAX_DELAY);
	status = HAL_I2C_IsDeviceReady(&hi2c1, 0x5C << 1, 3, 100);

	sprintf(msg, "ADDR5C status=%d\r\n", status);

	HAL_UART_Transmit(&huart2, (uint8_t*) msg, strlen(msg),
	HAL_MAX_DELAY);

	/* 최초 1회 UART 인터럽트 수신 엔진 시동 */
	HAL_UART_Receive_IT(&huart2, &rx_data, 1);
  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
	while (1) {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
		// 1. 조도 센서 데이터 수집
		HAL_I2C_Master_Receive(&hi2c1, 0x23 << 1, bh1750_data, 2,
				HAL_MAX_DELAY);
		raw = (bh1750_data[0] << 8) | bh1750_data[1];
		lux = raw / 1.2f;

		// 2. 파이썬 패킷 수신 완료 플래그 처리
		if (rx_flag == 1) {
			Parse_AI_Command(rx_buffer);
			rx_index = 0;
			rx_flag = 0;
		}

		// 🚨 [통신 락 방어 코드 추가]
		// 만약 시리얼 통신 도중 노이즈나 타이밍 충돌로 오버런 에러가 나면
		// 인터럽트가 죽어버리므로, 에러 레지스터를 강제로 초기화하고 수신 엔진을 재가동합니다.
		if (huart2.RxState == HAL_UART_STATE_READY) {
			HAL_UART_Receive_IT(&huart2, &rx_data, 1);
		}

		// 3. 현재 target_angle 기반 모터 위치 유지 및 구동
		uint16_t target_pwm = 500
				+ (uint16_t) ((float) target_angle * (2000.0f / 180.0f));
		Servo_SetPosition(target_pwm);

		// 4. 파이썬 수집용 데이터 출력 포맷
		sprintf(msg, "Lux: %.2f , PWM: %d\r\n", lux, target_pwm);
		HAL_UART_Transmit(&huart2, (uint8_t*) msg, strlen(msg), HAL_MAX_DELAY);

		// 통신 안정성을 위해 대기시간 유연하게 조정 (500ms)
		HAL_Delay(500);
	}
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE3);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
  RCC_OscInitStruct.PLL.PLLM = 8;
  RCC_OscInitStruct.PLL.PLLN = 50;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = 2;
  RCC_OscInitStruct.PLL.PLLR = 2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV2;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_1) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */
void Servo_SetPosition(uint16_t pwm) {
	__HAL_TIM_SET_COMPARE(&htim1, TIM_CHANNEL_1, pwm);
}
/* USER CODE END 4 */

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
	/* User can add his own implementation to report the HAL error return state */
	__disable_irq();
	while (1) {
	}
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
